from datetime import datetime
from decimal import Decimal

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base, utcnow_naive


class DeliveryZoneRule(Base):
    """Deterministic geographic/service rule attached to one delivery tariff zone."""

    __tablename__ = "delivery_zone_rules"
    __table_args__ = (
        UniqueConstraint("zone_id", name="uq_delivery_zone_rules_zone_id"),
        UniqueConstraint(
            "delivery_type",
            "priority",
            name="uq_delivery_zone_rules_type_priority",
        ),
        CheckConstraint("priority >= 0", name="ck_delivery_zone_rules_priority_nonnegative"),
        CheckConstraint("version >= 1", name="ck_delivery_zone_rules_version_positive"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    zone_id: Mapped[int] = mapped_column(
        ForeignKey("delivery_zones.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    delivery_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    priority: Mapped[int] = mapped_column(Integer, nullable=False)
    country_code: Mapped[str] = mapped_column(String(2), nullable=False, default="RU")
    region: Mapped[str] = mapped_column(String(160), nullable=False, default="")
    city: Mapped[str] = mapped_column(String(160), nullable=False, default="")
    postal_prefix: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    provider_code: Mapped[str] = mapped_column(String(64), nullable=False)
    service_code: Mapped[str] = mapped_column(String(96), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="RUB")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow_naive)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow_naive)


class DeliveryQuote(Base):
    """Immutable commercial quote accepted by checkout, never recomputed by shipment."""

    __tablename__ = "delivery_quotes"
    __table_args__ = (
        UniqueConstraint("public_id", name="uq_delivery_quotes_public_id"),
        UniqueConstraint("order_id", name="uq_delivery_quotes_order_id"),
        CheckConstraint("price >= 0", name="ck_delivery_quotes_price_nonnegative"),
        CheckConstraint(
            "status IN ('created','accepted','expired','cancelled')",
            name="ck_delivery_quotes_status",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    public_id: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), nullable=False, index=True)
    zone_id: Mapped[int | None] = mapped_column(ForeignKey("delivery_zones.id"), nullable=True, index=True)
    delivery_type: Mapped[str] = mapped_column(String(64), nullable=False)
    country_code: Mapped[str] = mapped_column(String(2), nullable=False, default="RU")
    region: Mapped[str] = mapped_column(String(160), nullable=False, default="")
    city: Mapped[str] = mapped_column(String(160), nullable=False, default="")
    postal_code: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    address_line: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    address_snapshot: Mapped[str] = mapped_column(String(700), nullable=False, default="")
    provider_code: Mapped[str] = mapped_column(String(64), nullable=False)
    service_code: Mapped[str] = mapped_column(String(96), nullable=False)
    price: Mapped[Decimal] = mapped_column(Numeric(20, 2, asdecimal=True), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="RUB")
    zone_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    quote_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="created", index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    order_id: Mapped[int | None] = mapped_column(ForeignKey("orders.id"), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow_naive)


class DeliveryShipmentAuthority(Base):
    """Immutable shipment-side copy of the quote commercial basis."""

    __tablename__ = "delivery_shipment_authorities"
    __table_args__ = (
        UniqueConstraint("shipment_id", name="uq_delivery_shipment_authorities_shipment"),
        UniqueConstraint("quote_id", name="uq_delivery_shipment_authorities_quote"),
        UniqueConstraint("order_id", name="uq_delivery_shipment_authorities_order"),
        CheckConstraint("price >= 0", name="ck_delivery_shipment_authorities_price_nonnegative"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    shipment_id: Mapped[int] = mapped_column(
        ForeignKey("delivery_shipments.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    quote_id: Mapped[int] = mapped_column(ForeignKey("delivery_quotes.id"), nullable=False, index=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), nullable=False, index=True)
    zone_id: Mapped[int | None] = mapped_column(ForeignKey("delivery_zones.id"), nullable=True)
    provider_code: Mapped[str] = mapped_column(String(64), nullable=False)
    service_code: Mapped[str] = mapped_column(String(96), nullable=False)
    price: Mapped[Decimal] = mapped_column(Numeric(20, 2, asdecimal=True), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    snapshot_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow_naive)
