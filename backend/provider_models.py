from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base, utcnow_naive


class ProviderCommand(Base):
    """Durable idempotent command for one external-provider side effect."""

    __tablename__ = "provider_commands"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'processing', 'sent', 'failed', 'review_required')",
            name="ck_provider_commands_status",
        ),
        CheckConstraint(
            "attempts >= 0",
            name="ck_provider_commands_attempts_nonnegative",
        ),
        UniqueConstraint(
            "provider",
            "idempotency_key",
            name="uq_provider_commands_provider_idempotency_key",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    provider: Mapped[str] = mapped_column(String(64), index=True)
    command_type: Mapped[str] = mapped_column(String(120), index=True)
    idempotency_key: Mapped[str] = mapped_column(String(255))
    aggregate_type: Mapped[str] = mapped_column(String(64), default="")
    aggregate_id: Mapped[str] = mapped_column(String(128), default="", index=True)
    payload_json: Mapped[str] = mapped_column(Text, default="{}")
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    lease_token: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    external_id: Mapped[str] = mapped_column(String(255), default="")
    last_error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, index=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
