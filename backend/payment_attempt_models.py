from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base


class PaymentCreationAttempt(Base):
    __tablename__ = "payment_creation_attempts"
    __table_args__ = (
        UniqueConstraint(
            "order_id",
            "provider",
            "attempt_number",
            name="uq_payment_creation_attempt_order_provider_number",
        ),
        CheckConstraint(
            "attempt_number > 0",
            name="ck_payment_creation_attempts_number_positive",
        ),
        CheckConstraint(
            "length(trim(provider)) > 0 AND provider = lower(trim(provider))",
            name="ck_payment_creation_attempts_provider_normalized",
        ),
        CheckConstraint(
            "status IN ('abandoned', 'completed', 'creating', 'retry_required', 'review_required')",
            name="ck_payment_creation_attempts_status_valid",
        ),
        CheckConstraint(
            "status <> 'creating' OR lease_expires_at IS NOT NULL",
            name="ck_payment_creation_attempts_creating_lease_required",
        ),
        CheckConstraint(
            "status = 'creating' OR lease_expires_at IS NULL",
            name="ck_payment_creation_attempts_noncreating_lease_empty",
        ),
        CheckConstraint(
            "status <> 'completed' OR length(trim(provider_payment_id)) > 0",
            name="ck_payment_creation_attempts_completed_provider_id_required",
        ),
        CheckConstraint(
            "status NOT IN ('abandoned', 'retry_required', 'review_required') OR length(trim(last_error)) > 0",
            name="ck_payment_creation_attempts_failure_error_required",
        ),
        Index(
            "uq_payment_creation_attempts_one_open",
            "order_id",
            "provider",
            unique=True,
            postgresql_where=text("status IN ('creating', 'retry_required', 'review_required')"),
            sqlite_where=text("status IN ('creating', 'retry_required', 'review_required')"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), index=True)
    provider: Mapped[str] = mapped_column(String(64), default="yookassa")
    attempt_number: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(64), default="creating", index=True)
    provider_payment_id: Mapped[str] = mapped_column(String(255), default="", index=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    last_error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )
