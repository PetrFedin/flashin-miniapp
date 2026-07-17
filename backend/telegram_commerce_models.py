from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


class TelegramOffer(Base):
    __tablename__ = "telegram_offers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(255))
    description: Mapped[str] = mapped_column(Text, default="")
    offer_type: Mapped[str] = mapped_column(String(40), index=True)  # club | certificate | drop
    stars_amount: Mapped[int] = mapped_column(Integer)
    duration_days: Mapped[int] = mapped_column(Integer, default=0)
    certificate_value: Mapped[int] = mapped_column(Integer, default=0)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    starts_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    ends_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class TelegramPurchase(Base):
    __tablename__ = "telegram_purchases"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), index=True)
    offer_id: Mapped[int] = mapped_column(ForeignKey("telegram_offers.id"), index=True)
    invoice_payload: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    invoice_url: Mapped[str] = mapped_column(String(2048), default="")
    stars_amount: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(32), default="invoice_created", index=True)
    recipient_telegram_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    recipient_username: Mapped[str] = mapped_column(String(255), default="")
    gift_message: Mapped[str] = mapped_column(Text, default="")
    telegram_payment_charge_id: Mapped[str] = mapped_column(String(255), default="", unique=True)
    provider_payment_charge_id: Mapped[str] = mapped_column(String(255), default="")
    paid_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    offer: Mapped[TelegramOffer] = relationship()


class GiftCertificate(Base):
    __tablename__ = "gift_certificates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    purchase_id: Mapped[int] = mapped_column(ForeignKey("telegram_purchases.id"), unique=True, index=True)
    code: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    value_rub: Mapped[int] = mapped_column(Integer)
    balance_rub: Mapped[int] = mapped_column(Integer)
    owner_customer_id: Mapped[int | None] = mapped_column(ForeignKey("customers.id"), nullable=True, index=True)
    recipient_telegram_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    recipient_username: Mapped[str] = mapped_column(String(255), default="")
    status: Mapped[str] = mapped_column(String(32), default="active", index=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    redeemed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class ClubMembership(Base):
    __tablename__ = "club_memberships"
    __table_args__ = (UniqueConstraint("customer_id", name="uq_club_membership_customer"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), index=True)
    level: Mapped[str] = mapped_column(String(32), default="club")
    status: Mapped[str] = mapped_column(String(32), default="active", index=True)
    starts_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    source_purchase_id: Mapped[int] = mapped_column(ForeignKey("telegram_purchases.id"), index=True)
    auto_renew: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class TelegramNotificationPreference(Base):
    __tablename__ = "telegram_notification_preferences"
    __table_args__ = (
        UniqueConstraint("customer_id", "event_type", name="uq_customer_notification_event"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), index=True)
    event_type: Mapped[str] = mapped_column(String(64), index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    quiet_hours_start: Mapped[str] = mapped_column(String(5), default="")
    quiet_hours_end: Mapped[str] = mapped_column(String(5), default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
