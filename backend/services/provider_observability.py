from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from ..database import utcnow_naive
from ..provider_models import ProviderCommand

PROVIDER_COMMAND_MONITORED_PROVIDERS = ("moysklad",)
PROVIDER_COMMAND_STATUSES = (
    "pending",
    "processing",
    "sent",
    "failed",
    "review_required",
)
PROVIDER_COMMAND_ACTIONABLE_STATUSES = (
    "pending",
    "processing",
    "failed",
    "review_required",
)


def _age_seconds(now: datetime, created_at: datetime | None) -> float:
    if created_at is None:
        return 0.0
    return max(0.0, (now - created_at).total_seconds())


def build_provider_command_status(
    db: Session,
    *,
    provider: str = "moysklad",
    now: datetime | None = None,
) -> dict[str, object]:
    """Return a bounded identifier-free operational snapshot for one provider."""
    normalized_provider = str(provider or "").strip().lower()
    if normalized_provider not in PROVIDER_COMMAND_MONITORED_PROVIDERS:
        raise ValueError("Unsupported monitored provider")
    effective_now = now or utcnow_naive()

    grouped = (
        db.query(
            ProviderCommand.status,
            func.count(ProviderCommand.id),
            func.min(ProviderCommand.created_at),
        )
        .filter(ProviderCommand.provider == normalized_provider)
        .group_by(ProviderCommand.status)
        .all()
    )
    counts = {status: 0 for status in PROVIDER_COMMAND_STATUSES}
    oldest_age_seconds = {status: 0.0 for status in PROVIDER_COMMAND_STATUSES}
    for status, count, oldest_created_at in grouped:
        normalized_status = str(status or "").strip().lower()
        if normalized_status not in counts:
            raise ValueError("Unsupported provider command status")
        counts[normalized_status] = int(count or 0)
        oldest_age_seconds[normalized_status] = _age_seconds(
            effective_now,
            oldest_created_at,
        )

    due_pending = (
        db.query(func.count(ProviderCommand.id))
        .filter(
            ProviderCommand.provider == normalized_provider,
            ProviderCommand.status == "pending",
            or_(
                ProviderCommand.next_attempt_at.is_(None),
                ProviderCommand.next_attempt_at <= effective_now,
            ),
        )
        .scalar()
        or 0
    )
    expired_processing = (
        db.query(func.count(ProviderCommand.id))
        .filter(
            ProviderCommand.provider == normalized_provider,
            ProviderCommand.status == "processing",
            or_(
                ProviderCommand.next_attempt_at.is_(None),
                ProviderCommand.next_attempt_at <= effective_now,
            ),
        )
        .scalar()
        or 0
    )

    actionable_count = sum(
        counts[status] for status in PROVIDER_COMMAND_ACTIONABLE_STATUSES
    )
    oldest_actionable_age = max(
        (oldest_age_seconds[status] for status in PROVIDER_COMMAND_ACTIONABLE_STATUSES),
        default=0.0,
    )
    return {
        "provider": normalized_provider,
        "counts": counts,
        "oldest_age_seconds": oldest_age_seconds,
        "due_pending": int(due_pending),
        "expired_processing": int(expired_processing),
        "actionable_count": int(actionable_count),
        "oldest_actionable_age_seconds": float(oldest_actionable_age),
    }
