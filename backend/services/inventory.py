from __future__ import annotations

import json
import math
from datetime import datetime

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..models import (
    AuditLog,
    InventoryAdjustment,
    InventorySnapshot,
    Order,
    OrderItem,
    ProductVariant,
)

_MAX_QUANTITY = 1_000_000
_MAX_PLANNING_DAYS = 3_650
_MIN_REASON_LENGTH = 3
_MAX_REASON_LENGTH = 255
_PAID_PAYMENT_STATUSES = ("paid", "partially_refunded")
_EXCLUDED_ORDER_STATUSES = ("cancelled", "refunded")


def _validate_positive_quantity(quantity: int) -> int:
    if isinstance(quantity, bool) or not isinstance(quantity, int):
        raise HTTPException(status_code=400, detail="Quantity must be an integer")
    if quantity <= 0 or quantity > _MAX_QUANTITY:
        raise HTTPException(status_code=400, detail=f"Quantity must be between 1 and {_MAX_QUANTITY}")
    return quantity


def _validate_stock_quantity(quantity: int) -> int:
    if isinstance(quantity, bool) or not isinstance(quantity, int):
        raise HTTPException(status_code=400, detail="Stock quantity must be an integer")
    if quantity < 0 or quantity > _MAX_QUANTITY:
        raise HTTPException(status_code=400, detail=f"Stock quantity must be between 0 and {_MAX_QUANTITY}")
    return quantity


def _validate_planning_days(value: int, *, field_name: str, allow_zero: bool) -> int:
    minimum = 0 if allow_zero else 1
    if isinstance(value, bool) or not isinstance(value, int):
        raise HTTPException(status_code=400, detail=f"{field_name} must be an integer")
    if value < minimum or value > _MAX_PLANNING_DAYS:
        raise HTTPException(status_code=400, detail=f"{field_name} must be between {minimum} and {_MAX_PLANNING_DAYS}")
    return value


def _validate_reason(reason: str) -> str:
    if not isinstance(reason, str):
        raise HTTPException(status_code=400, detail="Inventory reason must be text")
    normalized = reason.strip()
    if "\x00" in normalized:
        raise HTTPException(status_code=400, detail="Inventory reason contains invalid characters")
    if not (_MIN_REASON_LENGTH <= len(normalized) <= _MAX_REASON_LENGTH):
        raise HTTPException(
            status_code=400,
            detail=(
                f"Inventory reason must contain between {_MIN_REASON_LENGTH} "
                f"and {_MAX_REASON_LENGTH} characters"
            ),
        )
    return normalized


def _validate_variant_state(variant: ProductVariant) -> None:
    if variant.stock_qty < 0 or variant.reserved_qty < 0:
        raise HTTPException(status_code=409, detail="Inventory quantities cannot be negative")
    if variant.stock_qty > _MAX_QUANTITY or variant.reserved_qty > _MAX_QUANTITY:
        raise HTTPException(status_code=409, detail="Inventory quantities exceed the supported limit")
    if variant.reserved_qty > variant.stock_qty:
        raise HTTPException(status_code=409, detail="Reserved quantity exceeds stock")


def _load_locked_variant(db: Session, variant_id: int) -> ProductVariant:
    if isinstance(variant_id, bool) or not isinstance(variant_id, int) or variant_id <= 0:
        raise HTTPException(status_code=400, detail="Variant id must be a positive integer")
    variant = (
        db.query(ProductVariant)
        .filter(ProductVariant.id == variant_id)
        .with_for_update()
        .first()
    )
    if not variant:
        raise HTTPException(status_code=404, detail=f"Variant {variant_id} not found")
    _validate_variant_state(variant)
    return variant


def _record_movement(
    db: Session,
    *,
    variant: ProductVariant,
    action: str,
    stock_before: int,
    reserved_before: int,
    reason: str,
    admin_id: int | None = None,
) -> None:
    payload = {
        "reason": reason,
        "reserved_after": variant.reserved_qty,
        "reserved_before": reserved_before,
        "reserved_delta": variant.reserved_qty - reserved_before,
        "sku": variant.sku,
        "stock_after": variant.stock_qty,
        "stock_before": stock_before,
        "stock_delta": variant.stock_qty - stock_before,
    }
    db.add(
        AuditLog(
            admin_id=admin_id,
            action=f"inventory.{action}",
            entity_type="product_variant",
            entity_id=str(variant.id),
            payload=json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True),
        )
    )


def reserve_variant(db: Session, variant_id: int, quantity: int) -> ProductVariant:
    quantity = _validate_positive_quantity(quantity)
    variant = _load_locked_variant(db, variant_id)
    available_qty = variant.stock_qty - variant.reserved_qty
    if available_qty < quantity:
        raise HTTPException(status_code=409, detail=f"Size {variant.size} is out of stock")
    stock_before = variant.stock_qty
    reserved_before = variant.reserved_qty
    variant.reserved_qty += quantity
    _record_movement(
        db,
        variant=variant,
        action="reserve",
        stock_before=stock_before,
        reserved_before=reserved_before,
        reason="checkout reservation",
    )
    return variant


def release_variant(db: Session, variant_id: int, quantity: int) -> None:
    quantity = _validate_positive_quantity(quantity)
    variant = _load_locked_variant(db, variant_id)
    if variant.reserved_qty < quantity:
        raise HTTPException(status_code=409, detail="Reserved quantity mismatch")
    stock_before = variant.stock_qty
    reserved_before = variant.reserved_qty
    variant.reserved_qty -= quantity
    _record_movement(
        db,
        variant=variant,
        action="release",
        stock_before=stock_before,
        reserved_before=reserved_before,
        reason="reservation release",
    )


def commit_reserved_to_sold(db: Session, variant_id: int, quantity: int) -> None:
    quantity = _validate_positive_quantity(quantity)
    variant = _load_locked_variant(db, variant_id)
    if variant.reserved_qty < quantity:
        raise HTTPException(status_code=409, detail="Reserved quantity mismatch")
    if variant.stock_qty < quantity:
        raise HTTPException(status_code=409, detail="Inventory would become negative")
    stock_before = variant.stock_qty
    reserved_before = variant.reserved_qty
    variant.reserved_qty -= quantity
    variant.stock_qty -= quantity
    _record_movement(
        db,
        variant=variant,
        action="sale",
        stock_before=stock_before,
        reserved_before=reserved_before,
        reason="paid order sale",
    )


def adjust_stock(
    db: Session,
    variant_id: int,
    new_stock_qty: int,
    reason: str = "",
    admin_id: int | None = None,
) -> ProductVariant:
    new_stock_qty = _validate_stock_quantity(new_stock_qty)
    normalized_reason = _validate_reason(reason)
    variant = _load_locked_variant(db, variant_id)
    if new_stock_qty < variant.reserved_qty:
        raise HTTPException(status_code=409, detail="Stock cannot be lower than reserved quantity")
    old_stock_qty = variant.stock_qty
    old_reserved_qty = variant.reserved_qty
    variant.stock_qty = new_stock_qty
    db.add(
        InventoryAdjustment(
            variant_id=variant.id,
            old_stock_qty=old_stock_qty,
            new_stock_qty=new_stock_qty,
            reason=normalized_reason,
            admin_id=admin_id,
        )
    )
    _record_movement(
        db,
        variant=variant,
        action="adjust",
        stock_before=old_stock_qty,
        reserved_before=old_reserved_qty,
        reason=normalized_reason,
        admin_id=admin_id,
    )
    return variant


def snapshot_inventory(db: Session, source: str = "system") -> int:
    normalized_source = (source or "system").strip()
    if not normalized_source or len(normalized_source) > 64 or "\x00" in normalized_source:
        raise HTTPException(status_code=400, detail="Inventory snapshot source is invalid")
    variants = db.query(ProductVariant).all()
    for variant in variants:
        _validate_variant_state(variant)
        db.add(
            InventorySnapshot(
                variant_id=variant.id,
                stock_qty=variant.stock_qty,
                reserved_qty=variant.reserved_qty,
                source=normalized_source,
            )
        )
    return len(variants)


def restock_inventory(
    db: Session,
    date_from: datetime,
    date_to: datetime,
    *,
    lead_time_days: int = 14,
    safety_stock_days: int = 7,
) -> list[dict[str, object]]:
    if not isinstance(date_from, datetime) or not isinstance(date_to, datetime):
        raise HTTPException(status_code=400, detail="Restock period must use datetimes")
    if date_to < date_from:
        raise HTTPException(status_code=400, detail="Restock period end cannot precede its start")
    lead_time_days = _validate_planning_days(
        lead_time_days,
        field_name="Lead time days",
        allow_zero=False,
    )
    safety_stock_days = _validate_planning_days(
        safety_stock_days,
        field_name="Safety stock days",
        allow_zero=True,
    )
    period_days = (date_to.date() - date_from.date()).days + 1
    sold_rows = (
        db.query(OrderItem.variant_id, func.coalesce(func.sum(OrderItem.quantity), 0))
        .join(Order, Order.id == OrderItem.order_id)
        .filter(
            Order.created_at >= date_from,
            Order.created_at <= date_to,
            Order.payment_status.in_(_PAID_PAYMENT_STATUSES),
            ~Order.status.in_(_EXCLUDED_ORDER_STATUSES),
        )
        .group_by(OrderItem.variant_id)
        .all()
    )
    sold_by_variant = {variant_id: int(quantity or 0) for variant_id, quantity in sold_rows}
    variants = db.query(ProductVariant).order_by(ProductVariant.id).all()
    result: list[dict[str, object]] = []
    for variant in variants:
        _validate_variant_state(variant)
        sold_count = sold_by_variant.get(variant.id, 0)
        avg_daily_sales = sold_count / period_days
        lead_time_demand = math.ceil(avg_daily_sales * lead_time_days)
        safety_stock = math.ceil(avg_daily_sales * safety_stock_days)
        target_stock = lead_time_demand + safety_stock
        available_stock = variant.stock_qty - variant.reserved_qty
        restock_qty = max(target_stock - available_stock, 0)
        result.append(
            {
                "variant_id": variant.id,
                "sku": variant.sku,
                "size": variant.size,
                "period_start": date_from,
                "period_end": date_to,
                "period_days": period_days,
                "sold_count": sold_count,
                "avg_daily_sales": avg_daily_sales,
                "lead_time_days": lead_time_days,
                "safety_stock_days": safety_stock_days,
                "lead_time_demand": lead_time_demand,
                "safety_stock": safety_stock,
                "target_stock": target_stock,
                "current_stock": variant.stock_qty,
                "reserved_stock": variant.reserved_qty,
                "available_stock": available_stock,
                "restock_qty": restock_qty,
            }
        )
    return result
