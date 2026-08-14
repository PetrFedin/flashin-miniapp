from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base, utcnow_naive


class ProductDemandRequest(Base):
    __tablename__ = "product_demand_requests"
    __table_args__ = (
        CheckConstraint(
            "request_type IN ('preorder', 'made_to_order')",
            name="ck_product_demand_request_type",
        ),
        CheckConstraint(
            "status IN ('requested', 'contacted', 'confirmed', 'cancelled')",
            name="ck_product_demand_request_status",
        ),
        CheckConstraint(
            "quantity >= 1 AND quantity <= 10",
            name="ck_product_demand_request_quantity",
        ),
        UniqueConstraint(
            "active_request_key",
            name="uq_product_demand_active_request",
        ),
        Index("ix_product_demand_customer", "customer_id"),
        Index("ix_product_demand_product", "product_id"),
        Index("ix_product_demand_variant", "variant_id"),
        Index("ix_product_demand_type", "request_type"),
        Index("ix_product_demand_status", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    customer_id: Mapped[int] = mapped_column(
        ForeignKey("customers.id", ondelete="CASCADE")
    )
    product_id: Mapped[int] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE")
    )
    variant_id: Mapped[int | None] = mapped_column(
        ForeignKey("product_variants.id", ondelete="SET NULL"), nullable=True
    )
    request_type: Mapped[str] = mapped_column(String(32))
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    requested_size: Mapped[str] = mapped_column(String(32), default="")
    requested_color: Mapped[str] = mapped_column(String(64), default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(32), default="requested")
    admin_note: Mapped[str] = mapped_column(Text, default="")
    active_request_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow_naive, onupdate=utcnow_naive
    )
