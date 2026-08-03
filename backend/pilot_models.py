from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base, utcnow_naive


class PilotRuntimeState(Base):
    __tablename__ = "pilot_runtime_state"
    __table_args__ = (
        CheckConstraint(
            "status IN ('closed', 'active', 'stopped', 'completed')",
            name="ck_pilot_runtime_state_status",
        ),
        CheckConstraint(
            "max_orders BETWEEN 1 AND 20",
            name="ck_pilot_runtime_state_max_orders",
        ),
        CheckConstraint(
            "accepted_orders >= 0 AND accepted_orders <= max_orders",
            name="ck_pilot_runtime_state_accepted_orders",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    run_id: Mapped[str] = mapped_column(String(64), default="")
    status: Mapped[str] = mapped_column(String(16), default="closed", index=True)
    admission_sha256: Mapped[str] = mapped_column(String(64), default="")
    release_sha256: Mapped[str] = mapped_column(String(64), default="")
    pilot_state_created_at: Mapped[str] = mapped_column(String(64), default="")
    max_orders: Mapped[int] = mapped_column(Integer, default=20)
    accepted_orders: Mapped[int] = mapped_column(Integer, default=0)
    allowed_telegram_ids: Mapped[str] = mapped_column(Text, default="[]")
    opened_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    stopped_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    stop_reason: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=utcnow_naive,
        onupdate=utcnow_naive,
    )


class PilotOrderSlot(Base):
    __tablename__ = "pilot_order_slots"
    __table_args__ = (
        UniqueConstraint("run_id", "sequence", name="uq_pilot_order_slot_run_sequence"),
        CheckConstraint("sequence BETWEEN 1 AND 20", name="ck_pilot_order_slot_sequence"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[str] = mapped_column(String(64), index=True)
    sequence: Mapped[int] = mapped_column(Integer)
    order_id: Mapped[int] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"),
        unique=True,
        index=True,
    )
    customer_id: Mapped[int] = mapped_column(
        ForeignKey("customers.id", ondelete="RESTRICT"),
        index=True,
    )
    admission_sha256: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)
