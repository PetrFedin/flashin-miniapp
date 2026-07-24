from decimal import Decimal, ROUND_HALF_UP

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, joinedload

from ..config import get_settings
from ..database import get_db
from ..models import Cart, Customer, Order, OrderItem
from ..schemas import CheckoutIn, OrderOut
from ..security import get_current_customer
from ..services.delivery import calculate_delivery_price
from ..services.inventory import release_variant, reserve_variant
from ..services.promos import calculate_discount

router = APIRouter(prefix="/orders", tags=["orders"])
settings = get_settings()
_MONEY = Decimal("0.01")

ORDER_STATUSES = {
    "created",
    "payment_created",
    "paid",
    "assembling",
    "ready",
    "shipped",
    "completed",
    "cancelled",
    "refund_requested",
    "refunded",
}


def _money(value: float | int | Decimal | None) -> float:
    return float(Decimal(str(value or 0)).quantize(_MONEY, rounding=ROUND_HALF_UP))


def _normalize_checkout(payload: CheckoutIn) -> tuple[str, str, str, str, str]:
    name = payload.name.strip()
    phone = payload.phone.strip()
    delivery_type = payload.delivery_type.strip().lower()
    address = payload.address.strip()
    comment = payload.comment.strip()

    if not name:
        raise HTTPException(status_code=422, detail="Name is required")
    if not phone:
        raise HTTPException(status_code=422, detail="Phone is required")
    if delivery_type not in {"pickup", "courier"}:
        raise HTTPException(status_code=422, detail="Unsupported delivery type")
    if delivery_type == "courier" and not address:
        raise HTTPException(status_code=422, detail="Address is required for courier delivery")

    return name, phone, delivery_type, address, comment


def _active_cart(db: Session, customer_id: int) -> Cart | None:
    return (
        db.query(Cart)
        .options(
            joinedload(Cart.items).joinedload("product"),
            joinedload(Cart.items).joinedload("variant"),
            joinedload(Cart.promo_code),
        )
        .filter(Cart.customer_id == customer_id, Cart.status == "active")
        .with_for_update()
        .first()
    )


@router.post("/checkout", response_model=OrderOut)
def checkout(payload: CheckoutIn, customer: Customer = Depends(get_current_customer), db: Session = Depends(get_db)):
    name, phone, delivery_type, address, comment = _normalize_checkout(payload)
    cart = _active_cart(db, customer.id)
    if not cart or not cart.items:
        raise HTTPException(status_code=400, detail="Cart is empty")

    customer.first_name = name
    customer.phone = phone

    subtotal = Decimal("0")
    for item in cart.items:
        quantity = int(item.quantity or 0)
        if quantity <= 0:
            raise HTTPException(status_code=409, detail="Cart contains an invalid quantity")
        if not item.product or not item.product.active:
            raise HTTPException(status_code=409, detail="Cart contains an unavailable product")
        subtotal += Decimal(str(item.product.price or 0)) * quantity

    subtotal_value = _money(subtotal)
    promo_discount = _money(calculate_discount(cart.promo_code, subtotal_value) if cart.promo_code else 0)
    loyalty_points = max(float(cart.loyalty_points_to_redeem or 0), 0)
    loyalty_discount = _money(loyalty_points * settings.loyalty_point_value_rub)
    merchandise_discount = _money(min(promo_discount + loyalty_discount, subtotal_value))
    delivery_price = _money(calculate_delivery_price(db, delivery_type, address))
    final_amount = _money(max(subtotal_value - merchandise_discount, 0) + delivery_price)

    order = Order(
        customer_id=customer.id,
        promo_code_id=cart.promo_code_id,
        status="created",
        payment_status="pending",
        delivery_status="not_started",
        delivery_type=delivery_type,
        address=address,
        comment=comment,
        currency="RUB",
        discount_amount=merchandise_discount,
        loyalty_points_redeemed=loyalty_points,
        loyalty_discount_amount=loyalty_discount,
        referral_code=cart.referral_code,
        delivery_price=delivery_price,
        total_amount=final_amount,
    )
    db.add(order)

    try:
        db.flush()
        for cart_item in cart.items:
            quantity = int(cart_item.quantity)
            variant = reserve_variant(db, cart_item.variant_id, quantity)
            product = cart_item.product
            db.add(
                OrderItem(
                    order_id=order.id,
                    product_id=product.id,
                    variant_id=variant.id,
                    title=product.title,
                    size=variant.size,
                    quantity=quantity,
                    price=_money(product.price),
                )
            )

        if cart.promo_code:
            cart.promo_code.used_count = max(int(cart.promo_code.used_count or 0), 0) + 1
        cart.status = "converted"
        db.commit()
    except HTTPException:
        db.rollback()
        raise
    except SQLAlchemyError as exc:
        db.rollback()
        raise HTTPException(status_code=503, detail="Checkout could not be completed") from exc
    except Exception:
        db.rollback()
        raise

    return (
        db.query(Order)
        .options(joinedload(Order.items))
        .filter(Order.id == order.id, Order.customer_id == customer.id)
        .first()
    )


@router.get("", response_model=list[OrderOut])
def my_orders(customer: Customer = Depends(get_current_customer), db: Session = Depends(get_db)):
    return (
        db.query(Order)
        .options(joinedload(Order.items))
        .filter(Order.customer_id == customer.id)
        .order_by(Order.created_at.desc())
        .all()
    )


@router.get("/{order_id}", response_model=OrderOut)
def get_order(order_id: int, customer: Customer = Depends(get_current_customer), db: Session = Depends(get_db)):
    order = (
        db.query(Order)
        .options(joinedload(Order.items))
        .filter(Order.id == order_id, Order.customer_id == customer.id)
        .first()
    )
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    return order


@router.post("/{order_id}/cancel", response_model=OrderOut)
def cancel_order(order_id: int, customer: Customer = Depends(get_current_customer), db: Session = Depends(get_db)):
    order = (
        db.query(Order)
        .options(joinedload(Order.items), joinedload(Order.promo_code))
        .filter(Order.id == order_id, Order.customer_id == customer.id)
        .with_for_update()
        .first()
    )
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    if order.status == "cancelled":
        return order
    if order.payment_status == "paid":
        raise HTTPException(status_code=409, detail="Paid order cannot be cancelled here; create refund")
    if order.status == "completed":
        raise HTTPException(status_code=409, detail="Completed order cannot be cancelled")

    try:
        for item in order.items:
            release_variant(db, item.variant_id, item.quantity)
        if order.promo_code and int(order.promo_code.used_count or 0) > 0:
            order.promo_code.used_count -= 1
        order.status = "cancelled"
        order.payment_status = "cancelled"
        db.commit()
    except Exception:
        db.rollback()
        raise

    return order
