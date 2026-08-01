from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base, utcnow_naive

if TYPE_CHECKING:
    from .models import Notification


class NotificationDeliveryState(Base):
    __tablename__ = "notification_delivery_states"
    __table_args__ = (
        UniqueConstraint("notification_id", name="uq_notification_delivery_state_notification"),
        CheckConstraint("attempts >= 0", name="ck_notification_delivery_state_attempts_nonnegative"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    notification_id: Mapped[int] = mapped_column(
        ForeignKey("notifications.id", ondelete="CASCADE"),
        index=True,
    )
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_error: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)


class NotificationEventKey(Base):
    """Durable idempotency key for deterministic notification producers."""

    __tablename__ = "notification_event_keys"
    __table_args__ = (
        UniqueConstraint("event_key", name="uq_notification_event_keys_event_key"),
        UniqueConstraint("notification_id", name="uq_notification_event_keys_notification"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_key: Mapped[str] = mapped_column(String(255), index=True)
    notification_id: Mapped[int] = mapped_column(
        ForeignKey("notifications.id", ondelete="CASCADE"),
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)

    notification: Mapped["Notification"] = relationship("Notification")
