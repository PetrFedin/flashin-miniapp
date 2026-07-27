from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session, joinedload

from ..database import get_db
from ..models import Cart, CartItem, Customer, Order, OrderItem
from ..schemas import CheckoutIn, OrderOut
from ..security import get_current_customer
from ..services.delivery import calculate_delivery_price
from ..services.inventory import release_variant, reserve_variant
from ..services.promos import calculate_discount

router = APIRouter(prefix="/orders", tags=["orders"])

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
CANCELLABLE_ORDER_STATUSES = {"created", "payment_created"}


def _load_locked_active_cart(db: Session, customer_id: int) -> Cart | None:
    """Lock the active cart so concurrent checkout requests cannot convert it twice."""
    cart = (
        db.query(Cart)
        .filter(Cart.customer_id == customer_id, Cart.status == "active")
        .order_by(Cart.created_at.asc(), Cart.id.asc())
        .with_for_update()
        .first()
    )
    if not cart:
        return None

    return (
        db.query(Cart)
        .options(
            joinedload(Cart.items).joinedload(CartItem.product),
            joinedload(Cart.items).joinedload(CartItem.variant),
            joinedload(Cart.promo_code),
        )
        .filter(Cart.id == cart.id)
        .first()
    )


def _validate_cart_for_checkout(cart: Cart) -> None:
    if not cart.items:
        raise HTTPException(status_code=400, detail="Cart is empty")

    for item in cart.items:
        if item.quantity <= 0:
            raise HTTPException(status_code=409, detail=f"Invalid quantity for cart item {item.id}")
        if not item.product or not item.variant:
            raise HTTPException(status_code=409, detail=f"Cart item {item.id} is incomplete")
        if item.variant.product_id != item.product_id:
            raise HTTPException(status_code=409, detail=f"Variant mismatch for cart item {item.id}")
        if not item.product.active:
            raise HTTPException(status_code=409, detail=f"Product {item.product.title} is unavailable")
        if item.product.price < 0:
            raise HTTPException(status_code=409, detail=f"Invalid price for product {item.product.title}")


@router.post("/checkout", response_model=OrderOut)
def checkout(
    payload: CheckoutIn,
    customer: Customer = Depends(get_current_customer),
    db: Session = Depends(get_db),
):
    try:
        cart = _load_locked_active_cart(db, customer.id)
        if not cart:
            raise HTTPException(status_code=409, detail="No active cart available for checkout")

        _validate_cart_for_checkout(cart)

        customer.first_name = (payload.name or customer.first_name or "").strip()
        customer.phone = (payload.phone or customer.phone or "").strip()

        subtotal = sum(float(item.product.price) * item.quantity for item in cart.items)
        discount = float(calculate_discount(cart.promo_code, subtotal)) if cart.promo_code else 0.0
        discount = min(max(discount, 0.0), subtotal)

        loyalty_discount = min(max(float(cart.loyalty_points_to_redeem or 0), 0.0), subtotal - discount)
        delivery_price = float(calculate_delivery_price(db, payload.delivery_type, payload.address))
        if delivery_price < 0:
            raise HTTPException(status_code=409, detail="Delivery price cannot be negative")

        final_amount = subtotal - discount - loyalty_discount + delivery_price

        order = Order(
            customer_id=customer.id,
            promo_code_id=cart.promo_code_id,
            status="created",
            payment_status="pending",
            delivery_status="not_started",
            delivery_type=payload.delivery_type,
            address=payload.address,
            comment=payload.comment,
            currency="RUB",
            discount_amount=discount,
            loyalty_points_redeemed=loyalty_discount,
            loyalty_discount_amount=loyalty_discount,
            referral_code=cart.referral_code,
            delivery_price=delivery_price,
            total_amount=final_amount,
        )
        db.add(order)
        db.flush()

        for cart_item in cart.items:
            variant = reserve_variant(db, cart_item.variant_id, cart_item.quantity)
            product = cart_item.product
            db.add(
                OrderItem(
                    order_id=order.id,
                    product_id=product.id,
                    variant_id=variant.id,
                    title=product.title,
                    size=variant.size,
                    quantity=cart_item.quantity,
                    price=product.price,
                )
            )

        if cart.promo_code:
            cart.promo_code.used_count += 1

        cart.status = "converted"
        db.commit()
    except HTTPException:
        db.rollback()
        raise
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
    try:
        order = (
            db.query(Order)
            .filter(Order.id == order_id, Order.customer_id == customer.id)
            .with_for_update()
            .first()
        )
        if not order:
            raise HTTPException(status_code=404, detail="Order not found")

        if order.status == "cancelled":
            return (
                db.query(Order)
                .options(joinedload(Order.items))
                .filter(Order.id == order.id)
                .first()
            )
        if order.payment_status == "paid":
            raise HTTPException(status_code=409, detail="Paid order cannot be cancelled here; create refund")
        if order.status not in CANCELLABLE_ORDER_STATUSES:
            raise HTTPException(status_code=409, detail=f"Order in status {order.status} cannot be cancelled")

        order_with_items = (
            db.query(Order)
            .options(joinedload(Order.items))
            .filter(Order.id == order.id)
            .first()
        )
        for item in order_with_items.items:
            release_variant(db, item.variant_id, item.quantity)

        order.status = "cancelled"
        order.payment_status = "cancelled"
        db.commit()
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise

    return (
        db.query(Order)
        .options(joinedload(Order.items))
        .filter(Order.id == order_id, Order.customer_id == customer.id)
        .first()
    )
