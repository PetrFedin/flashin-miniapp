from datetime import datetime

from fastapi import HTTPException
from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    event,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base
from .models import Notification
from .notification_statuses import (
    MAX_NOTIFICATION_ATTEMPTS,
    MAX_NOTIFICATION_DEDUPLICATION_KEY_LENGTH,
    MAX_NOTIFICATION_ERROR_LENGTH,
    MAX_NOTIFICATION_LEASE_TOKEN_LENGTH,
    MAX_NOTIFICATION_MESSAGE_LENGTH,
    MAX_NOTIFICATION_TELEGRAM_ID_LENGTH,
    NOTIFICATION_DISCARDED,
    NOTIFICATION_FAILED,
    NOTIFICATION_SENT,
    VALID_NOTIFICATION_STATUSES,
    VALID_NOTIFICATION_STATUS_SQL,
    normalize_notification_deduplication_key,
    normalize_notification_error,
    normalize_notification_message,
    normalize_notification_status,
    normalize_telegram_id,
)


class NotificationDeliveryState(Base):
    __tablename__ = "notification_delivery_states"
    __table_args__ = (
        UniqueConstraint("notification_id", name="uq_notification_delivery_state_notification"),
        CheckConstraint(
            f"attempts BETWEEN 0 AND {MAX_NOTIFICATION_ATTEMPTS}",
            name="ck_notification_delivery_state_attempts_range",
        ),
        CheckConstraint(
            f"length(last_error) <= {MAX_NOTIFICATION_ERROR_LENGTH}",
            name="ck_notification_delivery_state_error_size",
        ),
        CheckConstraint(
            f"length(deduplication_key) <= {MAX_NOTIFICATION_DEDUPLICATION_KEY_LENGTH}",
            name="ck_notification_delivery_state_deduplication_key_size",
        ),
        CheckConstraint(
            "deduplication_key = trim(deduplication_key)",
            name="ck_notification_delivery_state_deduplication_key_normalized",
        ),
        CheckConstraint(
            f"length(lease_token) <= {MAX_NOTIFICATION_LEASE_TOKEN_LENGTH}",
            name="ck_notification_delivery_state_lease_token_size",
        ),
        CheckConstraint(
            "lease_token = trim(lease_token)",
            name="ck_notification_delivery_state_lease_token_normalized",
        ),
        CheckConstraint(
            "lease_token = '' OR next_attempt_at IS NOT NULL",
            name="ck_notification_delivery_state_lease_has_deadline",
        ),
        CheckConstraint(
            "attempts = 0 OR length(trim(last_error)) > 0",
            name="ck_notification_delivery_state_attempt_error_coherent",
        ),
        Index(
            "ix_notification_delivery_states_due",
            "next_attempt_at",
            "notification_id",
        ),
        Index(
            "uq_notification_delivery_states_deduplication_key",
            "deduplication_key",
            unique=True,
            postgresql_where=text("deduplication_key <> ''"),
            sqlite_where=text("deduplication_key <> ''"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    notification_id: Mapped[int] = mapped_column(
        ForeignKey("notifications.id", ondelete="CASCADE"),
        index=True,
    )
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_error: Mapped[str] = mapped_column(Text, default="")
    deduplication_key: Mapped[str] = mapped_column(String(255), default="")
    lease_token: Mapped[str] = mapped_column(String(64), default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


def _notification_constraint_names() -> set[str]:
    return {
        constraint.name
        for constraint in Notification.__table__.constraints
        if constraint.name
    }


def _append_notification_check(name: str, expression: str) -> None:
    if name not in _notification_constraint_names():
        Notification.__table__.append_constraint(CheckConstraint(expression, name=name))


def _apply_notification_constraints() -> None:
    _append_notification_check(
        "ck_notifications_status_valid",
        f"status IN ({VALID_NOTIFICATION_STATUS_SQL})",
    )
    _append_notification_check(
        "ck_notifications_telegram_id_size",
        f"length(telegram_id) BETWEEN 1 AND {MAX_NOTIFICATION_TELEGRAM_ID_LENGTH}",
    )
    _append_notification_check(
        "ck_notifications_telegram_id_normalized",
        "telegram_id = trim(telegram_id)",
    )
    _append_notification_check(
        "ck_notifications_message_size",
        f"length(message) BETWEEN 1 AND {MAX_NOTIFICATION_MESSAGE_LENGTH}",
    )
    _append_notification_check(
        "ck_notifications_message_normalized",
        "message = trim(message)",
    )
    _append_notification_check(
        "ck_notifications_error_size",
        f"length(error) <= {MAX_NOTIFICATION_ERROR_LENGTH}",
    )
    _append_notification_check(
        "ck_notifications_sent_state_coherent",
        "((status = 'sent' AND sent_at IS NOT NULL AND error = '') "
        "OR (status <> 'sent' AND sent_at IS NULL))",
    )
    _append_notification_check(
        "ck_notifications_terminal_error_required",
        "status NOT IN ('failed', 'discarded') OR length(trim(error)) > 0",
    )


def _validate_notification_before_write(_mapper, _connection, target: Notification) -> None:
    target.telegram_id = normalize_telegram_id(target.telegram_id)
    target.message = normalize_notification_message(target.message, truncate=False)
    target.status = normalize_notification_status(target.status)
    target.error = normalize_notification_error(target.error)

    if target.status not in VALID_NOTIFICATION_STATUSES:
        raise HTTPException(status_code=400, detail="Invalid notification status")
    if target.status == NOTIFICATION_SENT:
        if target.sent_at is None:
            raise HTTPException(status_code=400, detail="Sent notification requires sent_at")
        if target.error:
            raise HTTPException(status_code=400, detail="Sent notification cannot contain an error")
    elif target.sent_at is not None:
        raise HTTPException(status_code=400, detail="Only sent notifications can contain sent_at")
    if target.status in {NOTIFICATION_FAILED, NOTIFICATION_DISCARDED} and not target.error:
        raise HTTPException(status_code=400, detail="Terminal notification requires an error")


def _validate_delivery_state_before_write(
    _mapper,
    _connection,
    target: NotificationDeliveryState,
) -> None:
    try:
        target.attempts = int(target.attempts or 0)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="Notification attempts must be an integer") from exc
    if target.attempts < 0 or target.attempts > MAX_NOTIFICATION_ATTEMPTS:
        raise HTTPException(status_code=400, detail="Notification attempts are out of range")

    target.last_error = normalize_notification_error(target.last_error)
    target.deduplication_key = normalize_notification_deduplication_key(
        target.deduplication_key
    )
    target.lease_token = str(target.lease_token or "").strip()
    if len(target.lease_token) > MAX_NOTIFICATION_LEASE_TOKEN_LENGTH:
        raise HTTPException(status_code=400, detail="Notification lease token is too long")
    if target.lease_token and target.next_attempt_at is None:
        raise HTTPException(status_code=400, detail="Notification lease requires a deadline")
    if target.attempts and not target.last_error:
        raise HTTPException(status_code=400, detail="Failed notification attempt requires an error")


def _register_validation() -> None:
    for event_name in ("before_insert", "before_update"):
        if not event.contains(Notification, event_name, _validate_notification_before_write):
            event.listen(Notification, event_name, _validate_notification_before_write)
        if not event.contains(
            NotificationDeliveryState,
            event_name,
            _validate_delivery_state_before_write,
        ):
            event.listen(
                NotificationDeliveryState,
                event_name,
                _validate_delivery_state_before_write,
            )


_apply_notification_constraints()
_register_validation()
