from __future__ import annotations

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base, utcnow_naive


class ReturnRefundAllocation(Base):
    """Durable financial allocation evidence for one ReturnRequest.

    Financial allocation is accounting/client evidence only. It has no authority
    to mutate sellable inventory; physical stock authority remains in reverse logistics.
    """

    __tablename__ = "return_refund_allocations"
    __table_args__ = (
        UniqueConstraint(
            "return_request_id",
            "component_key",
            name="uq_return_refund_allocation_component",
        ),
        CheckConstraint(
            "component_kind IN ('item','delivery','goodwill')",
            name="ck_return_refund_allocation_kind",
        ),
        CheckConstraint(
            "amount_cents > 0",
            name="ck_return_refund_allocation_amount_positive",
        ),
        CheckConstraint(
            "quantity_evidence IS NULL OR quantity_evidence > 0",
            name="ck_return_refund_allocation_quantity_positive",
        ),
        CheckConstraint(
            "("
            "component_kind = 'item' AND order_item_id IS NOT NULL"
            ") OR ("
            "component_kind IN ('delivery','goodwill') "
            "AND order_item_id IS NULL AND quantity_evidence IS NULL"
            ")",
            name="ck_return_refund_allocation_component_shape",
        ),
        CheckConstraint(
            "policy_version > 0",
            name="ck_return_refund_allocation_policy_version",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    return_request_id: Mapped[int] = mapped_column(
        ForeignKey("return_requests.id", ondelete="CASCADE"),
        index=True,
    )
    order_id: Mapped[int] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"),
        index=True,
    )
    order_item_id: Mapped[int | None] = mapped_column(
        ForeignKey("order_items.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    component_kind: Mapped[str] = mapped_column(String(32), index=True)
    component_key: Mapped[str] = mapped_column(String(96))
    quantity_evidence: Mapped[int | None] = mapped_column(Integer, nullable=True)
    amount_cents: Mapped[int] = mapped_column(Integer)
    policy_version: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, index=True)
