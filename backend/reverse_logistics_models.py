from __future__ import annotations

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base, utcnow_naive


class ReturnLogisticsCase(Base):
    """Physical-return projection, deliberately separate from financial refund state."""

    __tablename__ = "return_logistics_cases"
    __table_args__ = (
        CheckConstraint(
            "status IN ('requested','authorized','in_transit','received','inspected')",
            name="ck_return_logistics_cases_status",
        ),
        UniqueConstraint("return_request_id", name="uq_return_logistics_case_return_request"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    return_request_id: Mapped[int] = mapped_column(
        ForeignKey("return_requests.id", ondelete="CASCADE"), index=True
    )
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), index=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(32), default="requested", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)


class ReturnLogisticsItem(Base):
    """Quantity authority for one original order line in a physical return."""

    __tablename__ = "return_logistics_items"
    __table_args__ = (
        UniqueConstraint("case_id", "order_item_id", name="uq_return_logistics_case_order_item"),
        CheckConstraint("ordered_qty > 0", name="ck_return_logistics_item_ordered_positive"),
        CheckConstraint("authorized_qty >= 0 AND authorized_qty <= ordered_qty", name="ck_return_logistics_item_authorized"),
        CheckConstraint("received_qty >= 0 AND received_qty <= authorized_qty", name="ck_return_logistics_item_received"),
        CheckConstraint("inspected_qty >= 0 AND inspected_qty <= received_qty", name="ck_return_logistics_item_inspected"),
        CheckConstraint("resalable_qty >= 0 AND damaged_qty >= 0 AND quarantine_qty >= 0", name="ck_return_logistics_item_dispositions_nonnegative"),
        CheckConstraint(
            "resalable_qty + damaged_qty + quarantine_qty = inspected_qty",
            name="ck_return_logistics_item_dispositions_match_inspected",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    case_id: Mapped[int] = mapped_column(
        ForeignKey("return_logistics_cases.id", ondelete="CASCADE"), index=True
    )
    order_item_id: Mapped[int] = mapped_column(ForeignKey("order_items.id"), index=True)
    variant_id: Mapped[int] = mapped_column(ForeignKey("product_variants.id"), index=True)
    ordered_qty: Mapped[int] = mapped_column(Integer)
    authorized_qty: Mapped[int] = mapped_column(Integer, default=0)
    received_qty: Mapped[int] = mapped_column(Integer, default=0)
    inspected_qty: Mapped[int] = mapped_column(Integer, default=0)
    resalable_qty: Mapped[int] = mapped_column(Integer, default=0)
    damaged_qty: Mapped[int] = mapped_column(Integer, default=0)
    quarantine_qty: Mapped[int] = mapped_column(Integer, default=0)


class ReturnLogisticsEvent(Base):
    """Append-only idempotency/evidence record for item-level physical mutations."""

    __tablename__ = "return_logistics_events"
    __table_args__ = (
        UniqueConstraint("item_id", "idempotency_key", name="uq_return_logistics_item_idempotency"),
        CheckConstraint(
            "event_type IN ('authorized','received','inspected')",
            name="ck_return_logistics_events_type",
        ),
        CheckConstraint(
            "disposition IN ('','resalable','damaged','quarantine')",
            name="ck_return_logistics_events_disposition",
        ),
        CheckConstraint("quantity > 0", name="ck_return_logistics_events_quantity_positive"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    case_id: Mapped[int] = mapped_column(
        ForeignKey("return_logistics_cases.id", ondelete="CASCADE"), index=True
    )
    item_id: Mapped[int] = mapped_column(
        ForeignKey("return_logistics_items.id", ondelete="CASCADE"), index=True
    )
    event_type: Mapped[str] = mapped_column(String(32), index=True)
    disposition: Mapped[str] = mapped_column(String(32), default="")
    quantity: Mapped[int] = mapped_column(Integer)
    idempotency_key: Mapped[str] = mapped_column(String(128))
    payload_hash: Mapped[str] = mapped_column(String(64))
    actor_admin_id: Mapped[int | None] = mapped_column(
        ForeignKey("admin_users.id"), nullable=True, index=True
    )
    reason: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, index=True)
