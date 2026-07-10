from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session, joinedload
from ..database import get_db
from ..models import Cart, Customer, Order, OrderItem
from ..schemas import CheckoutIn, OrderOut
from ..security import get_current_customer
from ..services.inventory import reserve_variant, release_variant
from ..services.promos import calculate_discount
from ..services.delivery import calculate_delivery_price

router = APIRouter(prefix="/orders", tags=["orders"])

ORDER_STATUSES = {"created", "payment_created", "paid", "assembling", "ready", "shipped", "completed", "cancelled", "refund_requested", "refunded"}


@router.post("/checkout", response_model=OrderOut)
def checkout(payload: CheckoutIn, customer: Customer = Depends(get_current_customer), db: Session = Depends(get_db)):
    cart = db.query(Cart).options(joinedload(Cart.items)).filter(Cart.customer_id == customer.id, Cart.status == "active").first()
    if not cart or not cart.items:
        raise HTTPException(status_code=400, detail="Cart is empty")

    customer.first_name = payload.name or customer.first_name
    customer.phone = payload.phone or customer.phone

    subtotal = sum(item.product.price * item.quantity for item in cart.items)
    discount = calculate_discount(cart.promo_code, subtotal) if cart.promo_code else 0
    loyalty_discount = cart.loyalty_points_to_redeem
    delivery_price = calculate_delivery_price(db, payload.delivery_type, payload.address)
    final_amount = max(subtotal - discount - loyalty_discount, 0) + delivery_price

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
        loyalty_points_redeemed=cart.loyalty_points_to_redeem,
        loyalty_discount_amount=loyalty_discount,
        referral_code=cart.referral_code,
        delivery_price=delivery_price,
        total_amount=final_amount,
    )
    db.add(order)
    db.flush()

    try:
        for cart_item in cart.items:
            variant = reserve_variant(db, cart_item.variant_id, cart_item.quantity)
            product = cart_item.product
            db.add(OrderItem(
                order_id=order.id,
                product_id=product.id,
                variant_id=variant.id,
                title=product.title,
                size=variant.size,
                quantity=cart_item.quantity,
                price=product.price,
            ))
        if cart.promo_code:
            cart.promo_code.used_count += 1
        cart.status = "converted"
        db.commit()
    except Exception:
        db.rollback()
        raise

    return db.query(Order).options(joinedload(Order.items)).filter(Order.id == order.id).first()


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
    order = db.query(Order).options(joinedload(Order.items)).filter(Order.id == order_id, Order.customer_id == customer.id).first()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    return order


@router.post("/{order_id}/cancel", response_model=OrderOut)
def cancel_order(order_id: int, customer: Customer = Depends(get_current_customer), db: Session = Depends(get_db)):
    order = db.query(Order).options(joinedload(Order.items)).filter(Order.id == order_id, Order.customer_id == customer.id).first()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    if order.payment_status == "paid":
        raise HTTPException(status_code=409, detail="Paid order cannot be cancelled here; create refund")
    if order.status in {"cancelled", "completed"}:
        return order
    for item in order.items:
        release_variant(db, item.variant_id, item.quantity)
    order.status = "cancelled"
    order.payment_status = "cancelled"
    db.commit()
    return order
