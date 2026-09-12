from __future__ import annotations

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base, utcnow_naive


class ProductIntentRequest(Base):
    """Non-payment customer intent for preorder / made-to-order merchandise.

    This is deliberately separate from Cart/Order. Creating an intent never
    reserves stock, creates a payment, or changes inventory. A normal order can
    only be created later through the existing stock-backed checkout path.
    """

    __tablename__ = "product_intent_requests"
    __table_args__ = (
        CheckConstraint(
            "intent_type IN ('preorder', 'made_to_order')",
            name="ck_product_intent_requests_type",
        ),
        CheckConstraint(
            "status IN ('requested', 'working', 'ready', 'closed', 'cancelled')",
            name="ck_product_intent_requests_status",
        ),
        CheckConstraint(
            "quantity >= 1 AND quantity <= 5",
            name="ck_product_intent_requests_quantity",
        ),
        CheckConstraint(
            "quote_amount IS NULL OR quote_amount >= 0",
            name="ck_product_intent_requests_quote_amount",
        ),
        UniqueConstraint("active_request_key", name="uq_product_intent_requests_active_key"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id", ondelete="CASCADE"), index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"), index=True)
    variant_id: Mapped[int | None] = mapped_column(
        ForeignKey("product_variants.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    intent_type: Mapped[str] = mapped_column(String(32), index=True)
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    requested_size: Mapped[str] = mapped_column(String(32), default="")
    requested_color: Mapped[str] = mapped_column(String(64), default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(32), default="requested", index=True)
    admin_note: Mapped[str] = mapped_column(Text, default="")
    quote_amount: Mapped[float | None] = mapped_column(Float, nullable=True)
    quote_currency: Mapped[str] = mapped_column(String(8), default="RUB")
    estimated_ready_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    active_request_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)
