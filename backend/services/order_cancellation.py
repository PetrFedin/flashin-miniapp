from collections import defaultdict
from typing import Literal

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ..database import utcnow_naive
from ..models import LoyaltyRedemptionHold, Order, Payment, PromoCode
from .inventory import release_variants
from .notifications import queue_order_status

CancellationSource = Literal["manual", "provider"]

_SETTLED_PAYMENT_STATUSES = {
    "paid",
    "paid_review_required",
    "refund_processing",
    "refund_pending",
    "refund_review_required",
    "partially_refunded",
    "refunded",
}
_MANUAL_ORDER_STATUSES = {"created"}
_MANUAL_PAYMENT_STATUSES = {"pending"}
_PROVIDER_ORDER_STATUSES = {"created", "payment_created"}
_PROVIDER_PAYMENT_STATUSES = {"pending", "payment_created"}


def _reservation_quantities(order: Order) -> dict[int, int]:
    quantities: dict[int, int] = defaultdict(int)
    for item in order.items:
        if isinstance(item.quantity, bool) or not isinstance(item.quantity, int) or item.quantity <= 0:
            raise HTTPException(
                status_code=409,
                detail=f"Order item {item.id} has invalid quantity",
            )
        if isinstance(item.variant_id, bool) or not isinstance(item.variant_id, int) or item.variant_id <= 0:
            raise HTTPException(
                status_code=409,
                detail=f"Order item {item.id} has invalid variant",
            )
        quantities[item.variant_id] += item.quantity
    if not quantities:
        raise HTTPException(status_code=409, detail="Order has no items to release")
    return dict(quantities)


def _validate_cancellation_state(
    db: Session,
    order: Order,
    source: CancellationSource,
) -> None:
    if order.payment_status in _SETTLED_PAYMENT_STATUSES:
        raise HTTPException(status_code=409, detail="Settled order requires refund flow")

    if source == "manual":
        if (
            order.status not in _MANUAL_ORDER_STATUSES
            or order.payment_status not in _MANUAL_PAYMENT_STATUSES
        ):
            raise HTTPException(
                status_code=409,
                detail="Order can be cancelled directly only before payment starts",
            )
        payment_exists = (
            db.query(Payment.id)
            .filter(Payment.order_id == order.id)
            .with_for_update()
            .first()
            is not None
        )
        if payment_exists:
            raise HTTPException(
                status_code=409,
                detail="Payment flow already exists; reconcile payment or use refund flow",
            )
        return

    if source == "provider":
        if (
            order.status not in _PROVIDER_ORDER_STATUSES
            or order.payment_status not in _PROVIDER_PAYMENT_STATUSES
        ):
            raise HTTPException(
                status_code=409,
                detail="Provider cancellation conflicts with the current order state",
            )
        return

    raise ValueError(f"Unsupported cancellation source: {source}")


def cancel_order_before_settlement(
    db: Session,
    order: Order,
    *,
    source: CancellationSource,
) -> bool:
    """Cancel an unpaid order exactly once inside the caller's transaction.

    The caller must hold a row lock for ``order``. All validation occurs before
    the first mutation so failures cannot leave a partially released order.
    """
    if order.status == "cancelled" and order.payment_status == "cancelled":
        return False
    if order.status == "cancelled" or order.payment_status == "cancelled":
        raise HTTPException(status_code=409, detail="Order cancellation state is inconsistent")

    _validate_cancellation_state(db, order, source)
    quantities = _reservation_quantities(order)

    promo = None
    if order.promo_code_id:
        promo = (
            db.query(PromoCode)
            .filter(PromoCode.id == order.promo_code_id)
            .with_for_update()
            .first()
        )

    holds = (
        db.query(LoyaltyRedemptionHold)
        .filter(
            LoyaltyRedemptionHold.customer_id == order.customer_id,
            LoyaltyRedemptionHold.order_id == order.id,
            LoyaltyRedemptionHold.status == "reserved",
        )
        .with_for_update()
        .all()
    )

    release_variants(
        db,
        quantities,
        order_id=order.id,
        source=f"order_cancellation:{source}",
    )

    if promo:
        promo.used_count = max(int(promo.used_count or 0) - 1, 0)

    released_at = utcnow_naive()
    for hold in holds:
        hold.status = "released"
        hold.released_at = released_at

    order.status = "cancelled"
    order.payment_status = "cancelled"
    order.delivery_status = "cancelled"
    queue_order_status(db, order)
    return True
