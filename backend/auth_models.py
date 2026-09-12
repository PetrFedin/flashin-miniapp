from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base, utcnow_naive


class TelegramAuthAssertion(Base):
    """Durable one-time claim for a verified Telegram Mini App bootstrap.

    Only keyed fingerprints and bounded metadata are persisted. Raw initData,
    query_id values, Telegram signatures and bearer tokens never enter this
    table.
    """

    __tablename__ = "telegram_auth_assertions"
    __table_args__ = (
        UniqueConstraint("assertion_hash", name="uq_telegram_auth_assertions_hash"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    assertion_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    auth_date: Mapped[int] = mapped_column(Integer, nullable=False)
    consumed_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=utcnow_naive,
    )
    retain_until: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)


class CustomerSession(Base):
    """Revocable customer session bound to one JWT session id and JTI."""

    __tablename__ = "customer_sessions"
    __table_args__ = (
        UniqueConstraint("session_id_hash", name="uq_customer_sessions_sid_hash"),
        UniqueConstraint("jti_hash", name="uq_customer_sessions_jti_hash"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    customer_id: Mapped[int] = mapped_column(
        ForeignKey("customers.id"),
        nullable=False,
        index=True,
    )
    session_id_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    jti_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=utcnow_naive,
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    revoke_reason: Mapped[str] = mapped_column(String(120), nullable=False, default="")


class CustomerAuthEvent(Base):
    """PII-free security audit event for customer authentication lifecycle."""

    __tablename__ = "customer_auth_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    customer_id: Mapped[int] = mapped_column(
        ForeignKey("customers.id"),
        nullable=False,
        index=True,
    )
    session_id: Mapped[int | None] = mapped_column(
        ForeignKey("customer_sessions.id"),
        nullable=True,
        index=True,
    )
    event_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=utcnow_naive,
    )
