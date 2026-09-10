from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base


class TelegramAuthConsumption(Base):
    __tablename__ = "telegram_auth_consumptions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    assertion_digest: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    query_id_digest: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True, index=True)
    auth_date_epoch: Mapped[int] = mapped_column(Integer, index=True)
    consumed_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


class CustomerSession(Base):
    __tablename__ = "customer_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    customer_id: Mapped[int] = mapped_column(
        ForeignKey("customers.id", ondelete="CASCADE"),
        index=True,
    )
    token_id_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
