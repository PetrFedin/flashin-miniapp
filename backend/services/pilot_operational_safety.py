from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from ..database import utcnow_naive
from ..models import BusinessEvent, Notification, WebhookOutbox
from ..notification_models import NotificationDeliveryState
from ..provider_models import ProviderCommand

DEFAULT_OPERATIONAL_QUEUE_GRACE_MINUTES = 15
MAX_OPERATIONAL_QUEUE_GRACE_MINUTES = 120
_PROVIDER_STATUSES = (
    "pending",
    "processing",
    "sent",
    "failed",
    "review_required",
)


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


def _scoped(query, model, created_since: datetime | None):
    if created_since is not None:
        query = query.filter(model.created_at >= created_since)
    return query


def _grouped_counts(
    db: Session,
    model,
    expected_statuses: tuple[str, ...],
    *,
    created_since: datetime | None,
) -> tuple[dict[str, int], int]:
    counts = {status: 0 for status in expected_statuses}
    unknown = 0
    query = db.query(model.status, func.count(model.id))
    query = _scoped(query, model, created_since)
    for status, count in query.group_by(model.status).all():
        normalized = str(status or "").strip().lower()
        if normalized in counts:
            counts[normalized] = int(count or 0)
        else:
            unknown += int(count or 0)
    return counts, unknown


def _oldest_age_seconds(
    db: Session,
    model,
    statuses: tuple[str, ...],
    now: datetime,
    *,
    created_since: datetime | None,
) -> float:
    query = db.query(func.min(model.created_at)).filter(model.status.in_(statuses))
    query = _scoped(query, model, created_since)
    return _age_seconds(now, query.scalar())


def _provider_queue(
    db: Session,
    *,
    now: datetime,
    overdue_before: datetime,
    created_since: datetime | None,
) -> tuple[dict[str, Any], list[str]]:
    counts, unknown = _grouped_counts(
        db,
        ProviderCommand,
        _PROVIDER_STATUSES,
        created_since=created_since,
    )
    # ProviderCommand may contain other durable providers later. The pilot gate
    # intentionally monitors only the MoySklad order/stock document spine.
    other_provider_counts = (
        db.query(ProviderCommand.status, func.count(ProviderCommand.id))
        .filter(ProviderCommand.provider != "moysklad")
    )
    other_provider_counts = _scoped(
        other_provider_counts,
        ProviderCommand,
        created_since,
    )
    other_total = sum(int(count or 0) for _status, count in other_provider_counts.group_by(ProviderCommand.status).all())
    if other_total:
        scoped_moysklad = db.query(
            ProviderCommand.status,
            func.count(ProviderCommand.id),
        ).filter(ProviderCommand.provider == "moysklad")
        scoped_moysklad = _scoped(scoped_moysklad, ProviderCommand, created_since)
        counts = {status: 0 for status in _PROVIDER_STATUSES}
        unknown = 0
        for status, count in scoped_moysklad.group_by(ProviderCommand.status).all():
            normalized = str(status or "").strip().lower()
            if normalized in counts:
                counts[normalized] = int(count or 0)
            else:
                unknown += int(count or 0)

    due = or_(
        ProviderCommand.next_attempt_at.is_(None),
        ProviderCommand.next_attempt_at <= now,
    )
    overdue_query = db.query(func.count(ProviderCommand.id)).filter(
        ProviderCommand.provider == "moysklad",
        ProviderCommand.status == "pending",
        ProviderCommand.created_at <= overdue_before,
        due,
    )
    overdue_query = _scoped(overdue_query, ProviderCommand, created_since)
    overdue_pending = int(overdue_query.scalar() or 0)

    due_query = db.query(func.count(ProviderCommand.id)).filter(
        ProviderCommand.provider == "moysklad",
        ProviderCommand.status == "pending",
        due,
    )
    due_query = _scoped(due_query, ProviderCommand, created_since)
    due_pending = int(due_query.scalar() or 0)

    expired_query = db.query(func.count(ProviderCommand.id)).filter(
        ProviderCommand.provider == "moysklad",
        ProviderCommand.status == "processing",
        due,
    )
    expired_query = _scoped(expired_query, ProviderCommand, created_since)
    expired_processing = int(expired_query.scalar() or 0)

    terminal = int(counts["failed"]) + int(counts["review_required"])
    blockers: list[str] = []
    if terminal:
        blockers.append("moysklad_command_terminal_failure")
    if expired_processing:
        blockers.append("moysklad_command_expired_lease")
    if overdue_pending:
        blockers.append("moysklad_command_overdue")
    if unknown:
        blockers.append("moysklad_command_unknown_status")

    return (
        {
            "counts": counts,
            "unknown_status": int(unknown),
            "due_pending": due_pending,
            "overdue_pending": overdue_pending,
            "expired_processing": expired_processing,
            "terminal": terminal,
            "oldest_actionable_age_seconds": _oldest_age_seconds(
                db,
                ProviderCommand,
                ("pending", "processing", "failed", "review_required"),
                now,
                created_since=created_since,
            ),
        },
        blockers,
    )


def _business_event_queue(
    db: Session,
    *,
    now: datetime,
    overdue_before: datetime,
    created_since: datetime | None,
) -> tuple[dict[str, Any], list[str]]:
    counts, unknown = _grouped_counts(
        db,
        BusinessEvent,
        ("pending", "processed", "failed"),
        created_since=created_since,
    )
    overdue_query = db.query(func.count(BusinessEvent.id)).filter(
        BusinessEvent.status == "pending",
        BusinessEvent.created_at <= overdue_before,
    )
    overdue_query = _scoped(overdue_query, BusinessEvent, created_since)
    overdue_pending = int(overdue_query.scalar() or 0)

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
            "overdue_pending": overdue_pending,
            "oldest_actionable_age_seconds": _oldest_age_seconds(
                db,
                BusinessEvent,
                ("pending", "failed"),
                now,
                created_since=created_since,
            ),
        },
        blockers,
    )


def _webhook_queue(
    db: Session,
    *,
    now: datetime,
    overdue_before: datetime,
    created_since: datetime | None,
) -> tuple[dict[str, Any], list[str]]:
    counts, unknown = _grouped_counts(
        db,
        WebhookOutbox,
        ("pending", "processing", "sent", "failed"),
        created_since=created_since,
    )
    due = or_(
        WebhookOutbox.next_attempt_at.is_(None),
        WebhookOutbox.next_attempt_at <= now,
    )
    overdue_query = db.query(func.count(WebhookOutbox.id)).filter(
        WebhookOutbox.status == "pending",
        WebhookOutbox.created_at <= overdue_before,
        due,
    )
    overdue_query = _scoped(overdue_query, WebhookOutbox, created_since)
    overdue_pending = int(overdue_query.scalar() or 0)

    expired_query = db.query(func.count(WebhookOutbox.id)).filter(
        WebhookOutbox.status == "processing",
        due,
    )
    expired_query = _scoped(expired_query, WebhookOutbox, created_since)
    expired_processing = int(expired_query.scalar() or 0)

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
            "overdue_pending": overdue_pending,
            "expired_processing": expired_processing,
            "oldest_actionable_age_seconds": _oldest_age_seconds(
                db,
                WebhookOutbox,
                ("pending", "processing", "failed"),
                now,
                created_since=created_since,
            ),
        },
        blockers,
    )


def _notification_queue(
    db: Session,
    *,
    now: datetime,
    overdue_before: datetime,
    created_since: datetime | None,
) -> tuple[dict[str, Any], list[str]]:
    counts, unknown = _grouped_counts(
        db,
        Notification,
        ("pending", "processing", "sent", "failed"),
        created_since=created_since,
    )
    due = or_(
        NotificationDeliveryState.id.is_(None),
        NotificationDeliveryState.next_attempt_at.is_(None),
        NotificationDeliveryState.next_attempt_at <= now,
    )
    overdue_query = (
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
    )
    overdue_query = _scoped(overdue_query, Notification, created_since)
    overdue_pending = int(overdue_query.scalar() or 0)

    expired_query = (
        db.query(func.count(Notification.id))
        .outerjoin(
            NotificationDeliveryState,
            NotificationDeliveryState.notification_id == Notification.id,
        )
        .filter(
            Notification.status == "processing",
            due,
        )
    )
    expired_query = _scoped(expired_query, Notification, created_since)
    expired_processing = int(expired_query.scalar() or 0)

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
            "overdue_pending": overdue_pending,
            "expired_processing": expired_processing,
            "oldest_actionable_age_seconds": _oldest_age_seconds(
                db,
                Notification,
                ("pending", "processing", "failed"),
                now,
                created_since=created_since,
            ),
        },
        blockers,
    )


def build_pilot_operational_safety(
    db: Session,
    *,
    grace_minutes: int = DEFAULT_OPERATIONAL_QUEUE_GRACE_MINUTES,
    created_since: datetime | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Return an identifier-free, fail-closed view of current-pilot queues.

    ``created_since`` should be the runtime ``opened_at`` timestamp. This keeps
    historical failures from a previous pilot from blocking a new run while a
    stopped/resumed run retains the same window and therefore cannot evade its
    unresolved delivery failures. Fresh work is visible but non-blocking.
    Terminal failures, expired leases, unknown states and work that remained due
    beyond the grace window block new pilot checkouts.
    """
    normalized_grace = _grace_minutes(grace_minutes)
    effective_now = now or utcnow_naive()
    if created_since is not None and created_since > effective_now:
        raise ValueError("Pilot operational queue scope cannot start in the future")
    overdue_before = effective_now - timedelta(minutes=normalized_grace)

    provider, provider_blockers = _provider_queue(
        db,
        now=effective_now,
        overdue_before=overdue_before,
        created_since=created_since,
    )
    events, event_blockers = _business_event_queue(
        db,
        now=effective_now,
        overdue_before=overdue_before,
        created_since=created_since,
    )
    webhooks, webhook_blockers = _webhook_queue(
        db,
        now=effective_now,
        overdue_before=overdue_before,
        created_since=created_since,
    )
    notifications, notification_blockers = _notification_queue(
        db,
        now=effective_now,
        overdue_before=overdue_before,
        created_since=created_since,
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
        "scope_started_at": created_since.isoformat() if created_since else None,
        "queues": {
            "moysklad_commands": provider,
            "business_events": events,
            "webhook_outbox": webhooks,
            "telegram_notifications": notifications,
        },
    }
