from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base


class BusinessEventRecoveryState(Base):
    """Durable diagnostics and manual-replay metadata for one business event."""

    __tablename__ = "business_event_recovery_states"

    business_event_id: Mapped[int] = mapped_column(
        ForeignKey("business_events.id", ondelete="CASCADE"),
        primary_key=True,
    )
    last_error: Mapped[str] = mapped_column(Text, default="")
    last_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
        index=True,
    )
    failed_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
        index=True,
    )
    replay_count: Mapped[int] = mapped_column(Integer, default=0)
    last_replayed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_replayed_by_admin_id: Mapped[int | None] = mapped_column(
        ForeignKey("admin_users.id", ondelete="SET NULL"),
        nullable=True,
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
