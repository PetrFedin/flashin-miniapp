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
    lease_token: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
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


class NotificationPolicyContext(Base):
    """Purpose/customer binding used by transport-time delivery policy checks.

    Existing notifications intentionally have no policy row and are treated as
    legacy transactional messages. Every notification created through the
    canonical queue helper gets an explicit context going forward.
    """

    __tablename__ = "notification_policy_contexts"
    __table_args__ = (
        CheckConstraint(
            "purpose IN ('transactional', 'marketing')",
            name="ck_notification_policy_context_purpose",
        ),
    )

    notification_id: Mapped[int] = mapped_column(
        ForeignKey("notifications.id", ondelete="CASCADE"),
        primary_key=True,
    )
    purpose: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    customer_id: Mapped[int | None] = mapped_column(
        ForeignKey("customers.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    campaign_id: Mapped[int | None] = mapped_column(
        ForeignKey("marketing_campaigns.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, nullable=False)

    notification: Mapped["Notification"] = relationship("Notification")
