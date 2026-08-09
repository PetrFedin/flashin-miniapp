from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from ..database import utcnow_naive
from ..models import BusinessEvent, Notification, WebhookOutbox
from ..notification_models import NotificationDeliveryState
from ..provider_models import ProviderCommand
from .provider_observability import build_provider_command_status

DEFAULT_OPERATIONAL_QUEUE_GRACE_MINUTES = 15
MAX_OPERATIONAL_QUEUE_GRACE_MINUTES = 120


def _grace_minutes(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("Pilot operational queue grace must be an integer")
    if value < 1 or value > MAX_OPERATIONAL_QUEUE_GRACE_MINUTES:
        raise ValueError(
            "Pilot operational queue grace must be between 1 and "
            f"{MAX_OPERATIONAL_QUEUE_GRACE_MINUTES} minutes"
        )
    return value


def _age_seconds(now: datetime, created_at: datetime | None) -> float:
    if created_at is None:
        return 0.0
    return max(0.0, (now - created_at).total_seconds())


def _grouped_counts(
    db: Session,
    model,
    expected_statuses: tuple[str, ...],
) -> tuple[dict[str, int], int]:
    counts = {status: 0 for status in expected_statuses}
    unknown = 0
    for status, count in db.query(model.status, func.count(model.id)).group_by(model.status).all():
        normalized = str(status or "").strip().lower()
        if normalized in counts:
            counts[normalized] = int(count or 0)
        else:
            unknown += int(count or 0)
    return counts, unknown


def _oldest_age_seconds(db: Session, model, statuses: tuple[str, ...], now: datetime) -> float:
    oldest = (
        db.query(func.min(model.created_at))
        .filter(model.status.in_(statuses))
        .scalar()
    )
    return _age_seconds(now, oldest)


def _provider_queue(
    db: Session,
    *,
    now: datetime,
    overdue_before: datetime,
) -> tuple[dict[str, Any], list[str]]:
    status = build_provider_command_status(db, provider="moysklad", now=now)
    counts = dict(status["counts"])
    overdue_pending = (
        db.query(func.count(ProviderCommand.id))
        .filter(
            ProviderCommand.provider == "moysklad",
            ProviderCommand.status == "pending",
            ProviderCommand.created_at <= overdue_before,
            or_(
                ProviderCommand.next_attempt_at.is_(None),
                ProviderCommand.next_attempt_at <= now,
            ),
        )
        .scalar()
        or 0
    )
    terminal = int(counts["failed"]) + int(counts["review_required"])
    expired_processing = int(status["expired_processing"])

    blockers: list[str] = []
    if terminal:
        blockers.append("moysklad_command_terminal_failure")
    if expired_processing:
        blockers.append("moysklad_command_expired_lease")
    if overdue_pending:
        blockers.append("moysklad_command_overdue")

    return (
        {
            "counts": counts,
            "due_pending": int(status["due_pending"]),
            "overdue_pending": int(overdue_pending),
            "expired_processing": expired_processing,
            "terminal": terminal,
            "oldest_actionable_age_seconds": float(
                status["oldest_actionable_age_seconds"]
            ),
        },
        blockers,
    )


def _business_event_queue(
    db: Session,
    *,
    now: datetime,
    overdue_before: datetime,
) -> tuple[dict[str, Any], list[str]]:
    counts, unknown = _grouped_counts(
        db,
        BusinessEvent,
        ("pending", "processed", "failed"),
    )
    overdue_pending = (
        db.query(func.count(BusinessEvent.id))
        .filter(
            BusinessEvent.status == "pending",
            BusinessEvent.created_at <= overdue_before,
        )
        .scalar()
        or 0
    )

    blockers: list[str] = []
    if counts["failed"]:
        blockers.append("business_event_terminal_failure")
    if overdue_pending:
        blockers.append("business_event_overdue")
    if unknown:
        blockers.append("business_event_unknown_status")

    return (
        {
            "counts": counts,
            "unknown_status": int(unknown),
            "overdue_pending": int(overdue_pending),
            "oldest_actionable_age_seconds": _oldest_age_seconds(
                db,
                BusinessEvent,
                ("pending", "failed"),
                now,
            ),
        },
        blockers,
    )


def _webhook_queue(
    db: Session,
    *,
    now: datetime,
    overdue_before: datetime,
) -> tuple[dict[str, Any], list[str]]:
    counts, unknown = _grouped_counts(
        db,
        WebhookOutbox,
        ("pending", "processing", "sent", "failed"),
    )
    due = or_(
        WebhookOutbox.next_attempt_at.is_(None),
        WebhookOutbox.next_attempt_at <= now,
    )
    overdue_pending = (
        db.query(func.count(WebhookOutbox.id))
        .filter(
            WebhookOutbox.status == "pending",
            WebhookOutbox.created_at <= overdue_before,
            due,
        )
        .scalar()
        or 0
    )
    expired_processing = (
        db.query(func.count(WebhookOutbox.id))
        .filter(
            WebhookOutbox.status == "processing",
            due,
        )
        .scalar()
        or 0
    )

    blockers: list[str] = []
    if counts["failed"]:
        blockers.append("webhook_outbox_terminal_failure")
    if expired_processing:
        blockers.append("webhook_outbox_expired_lease")
    if overdue_pending:
        blockers.append("webhook_outbox_overdue")
    if unknown:
        blockers.append("webhook_outbox_unknown_status")

    return (
        {
            "counts": counts,
            "unknown_status": int(unknown),
            "overdue_pending": int(overdue_pending),
            "expired_processing": int(expired_processing),
            "oldest_actionable_age_seconds": _oldest_age_seconds(
                db,
                WebhookOutbox,
                ("pending", "processing", "failed"),
                now,
            ),
        },
        blockers,
    )


def _notification_queue(
    db: Session,
    *,
    now: datetime,
    overdue_before: datetime,
) -> tuple[dict[str, Any], list[str]]:
    counts, unknown = _grouped_counts(
        db,
        Notification,
        ("pending", "processing", "sent", "failed"),
    )
    due = or_(
        NotificationDeliveryState.id.is_(None),
        NotificationDeliveryState.next_attempt_at.is_(None),
        NotificationDeliveryState.next_attempt_at <= now,
    )
    overdue_pending = (
        db.query(func.count(Notification.id))
        .outerjoin(
            NotificationDeliveryState,
            NotificationDeliveryState.notification_id == Notification.id,
        )
        .filter(
            Notification.status == "pending",
            Notification.created_at <= overdue_before,
            due,
        )
        .scalar()
        or 0
    )
    expired_processing = (
        db.query(func.count(Notification.id))
        .outerjoin(
            NotificationDeliveryState,
            NotificationDeliveryState.notification_id == Notification.id,
        )
        .filter(
            Notification.status == "processing",
            due,
        )
        .scalar()
        or 0
    )

    blockers: list[str] = []
    if counts["failed"]:
        blockers.append("notification_terminal_failure")
    if expired_processing:
        blockers.append("notification_expired_lease")
    if overdue_pending:
        blockers.append("notification_overdue")
    if unknown:
        blockers.append("notification_unknown_status")

    return (
        {
            "counts": counts,
            "unknown_status": int(unknown),
            "overdue_pending": int(overdue_pending),
            "expired_processing": int(expired_processing),
            "oldest_actionable_age_seconds": _oldest_age_seconds(
                db,
                Notification,
                ("pending", "processing", "failed"),
                now,
            ),
        },
        blockers,
    )


def build_pilot_operational_safety(
    db: Session,
    *,
    grace_minutes: int = DEFAULT_OPERATIONAL_QUEUE_GRACE_MINUTES,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Return an identifier-free, fail-closed view of pilot delivery queues.

    Fresh work is visible but non-blocking. Terminal failures, expired leases,
    unknown states and work that remained due beyond the grace window block new
    pilot checkouts until an operator resolves or replays the affected records.
    """
    normalized_grace = _grace_minutes(grace_minutes)
    effective_now = now or utcnow_naive()
    overdue_before = effective_now - timedelta(minutes=normalized_grace)

    provider, provider_blockers = _provider_queue(
        db,
        now=effective_now,
        overdue_before=overdue_before,
    )
    events, event_blockers = _business_event_queue(
        db,
        now=effective_now,
        overdue_before=overdue_before,
    )
    webhooks, webhook_blockers = _webhook_queue(
        db,
        now=effective_now,
        overdue_before=overdue_before,
    )
    notifications, notification_blockers = _notification_queue(
        db,
        now=effective_now,
        overdue_before=overdue_before,
    )

    blocking_codes = list(
        dict.fromkeys(
            provider_blockers
            + event_blockers
            + webhook_blockers
            + notification_blockers
        )
    )
    return {
        "healthy": not blocking_codes,
        "blocking_codes": blocking_codes,
        "grace_minutes": normalized_grace,
        "queues": {
            "moysklad_commands": provider,
            "business_events": events,
            "webhook_outbox": webhooks,
            "telegram_notifications": notifications,
        },
    }
