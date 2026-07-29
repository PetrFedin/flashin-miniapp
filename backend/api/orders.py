import hashlib
import json
import re
from datetime import datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, Header, HTTPException, Response
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from ..checkout_idempotency_models import CheckoutIdempotency
from ..config import get_settings
from ..database import get_db
from ..models import (
    Cart,
    CartItem,
    CrmProfile,
    Customer,
    LoyaltyRedemptionHold,
    Order,
    OrderItem,
    PromoCode,
)
from ..schemas import CheckoutIn, OrderOut
from ..security import get_current_customer
from ..services.delivery import calculate_delivery_price
from ..services.inventory import release_variant, reserve_variant
from ..services.pricing import calculate_pricing, money, points

router = APIRouter(prefix="/orders", tags=["orders"])

_IDEMPOTENCY_KEY_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{16,128}$")
CANCELLABLE_ORDER_STATUSES = {"created", "payment_created"}
_money = money


def _clean_required(value: str, field: str, max_length: int) -> str:
    cleaned = (value or "").strip()
    if not cleaned:
        raise HTTPException(status_code=400, detail=f"{field} is required")
    if len(cleaned) > max_length:
        raise HTTPException(status_code=400, detail=f"{field} is too long")
    return cleaned


def _clean_idempotency_key(value: str) -> str:
    key = (value or "").strip()
    if not _IDEMPOTENCY_KEY_PATTERN.fullmatch(key):
        raise HTTPException(
            status_code=400,
            detail=(
                "Idempotency-Key must be 16-128 characters and contain only "
                "letters, digits, dot, underscore, colon, or hyphen"
            ),
        )
    return key


def _checkout_fingerprint(payload: CheckoutIn) -> str:
    normalized = {
        "name": (payload.name or "").strip(),
        "phone": (payload.phone or "").strip(),
        "delivery_type": (payload.delivery_type or "").strip().lower(),
        "address": (payload.address or "").strip(),
        "comment": (payload.comment or "").strip()[:2000],
    }
    canonical = json.dumps(
        normalized,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _load_customer_order(db: Session, order_id: int, customer_id: int) -> Order:
    order = (
        db.query(Order)
        .options(joinedload(Order.items))
        .filter(Order.id == order_id, Order.customer_id == customer_id)
        .first()
    )
    if not order:
        raise HTTPException(status_code=409, detail="Idempotent checkout references a missing order")
    return order


def _validate_idempotency_record(
    db: Session,
    record: CheckoutIdempotency,
    fingerprint: str,
    customer_id: int,
) -> Order:
    if record.request_fingerprint != fingerprint:
        raise HTTPException(
            status_code=409,
            detail="Idempotency-Key was already used with different checkout data",
        )
    if not record.order_id:
        raise HTTPException(status_code=409, detail="Checkout request is already in progress")
    return _load_customer_order(db, record.order_id, customer_id)


def _load_locked_active_cart(db: Session, customer_id: int) -> Cart | None:
    carts = (
        db.query(Cart)
        .filter(Cart.customer_id == customer_id, Cart.status == "active")
        .order_by(Cart.created_at.asc(), Cart.id.asc())
        .with_for_update()
        .all()
    )
    if not carts:
        return None
    if len(carts) > 1:
        raise HTTPException(status_code=409, detail="Multiple active carts require reconciliation")

    return (
        db.query(Cart)
        .options(
            joinedload(Cart.items).joinedload(CartItem.product),
            joinedload(Cart.items).joinedload(CartItem.variant),
            joinedload(Cart.promo_code),
        )
        .filter(Cart.id == carts[0].id)
        .first()
    )


def _validate_cart_for_checkout(cart: Cart) -> None:
    if not cart.items:
        raise HTTPException(status_code=400, detail="Cart is empty")

    seen_variants: set[int] = set()
    for item in cart.items:
        if isinstance(item.quantity, bool) or not isinstance(item.quantity, int) or item.quantity <= 0:
            raise HTTPException(status_code=409, detail=f"Invalid quantity for cart item {item.id}")
        if not item.product or not item.variant:
            raise HTTPException(status_code=409, detail=f"Cart item {item.id} is incomplete")
        if item.variant.product_id != item.product_id or item.product.id != item.product_id:
            raise HTTPException(status_code=409, detail=f"Variant mismatch for cart item {item.id}")
        if item.variant_id in seen_variants:
            raise HTTPException(status_code=409, detail=f"Duplicate variant in cart: {item.variant_id}")
        seen_variants.add(item.variant_id)
        if not item.product.active:
            raise HTTPException(status_code=409, detail=f"Product {item.product.title} is unavailable")
        if money(item.product.price, "product price") < 0:
            raise HTTPException(status_code=409, detail=f"Invalid price for product {item.product.title}")


def _lock_promo(db: Session, cart: Cart) -> PromoCode | None:
    if not cart.promo_code_id:
        return None
    promo = (
        db.query(PromoCode)
        .filter(PromoCode.id == cart.promo_code_id)
        .with_for_update()
        .first()
    )
    if not promo:
        raise HTTPException(status_code=409, detail="Applied promo code no longer exists")
    return promo


def _lock_and_validate_loyalty(
    db: Session,
    cart: Cart,
    customer_id: int,
    subtotal: Decimal,
    promo: PromoCode | None,
) -> tuple[Decimal, LoyaltyRedemptionHold | None]:
    requested_points = points(cart.loyalty_points_to_redeem or 0)
    if requested_points == 0:
        return Decimal("0.0000"), None

    profile = (
        db.query(CrmProfile)
        .filter(CrmProfile.customer_id == customer_id)
        .with_for_update()
        .first()
    )
    if not profile:
        raise HTTPException(status_code=409, detail="Loyalty profile not found")

    holds = (
        db.query(LoyaltyRedemptionHold)
        .filter(
            LoyaltyRedemptionHold.customer_id == customer_id,
            LoyaltyRedemptionHold.status == "reserved",
        )
        .with_for_update()
        .all()
    )
    current_hold = next((hold for hold in holds if hold.cart_id == cart.id), None)
    other_reserved_points = sum(
        (points(hold.points, "reserved loyalty points") for hold in holds if hold.cart_id != cart.id),
        Decimal("0.0000"),
    )
    available_points = points(profile.loyalty_points, "loyalty balance") - other_reserved_points
    if requested_points > available_points:
        raise HTTPException(status_code=409, detail="Not enough available loyalty points")

    settings = get_settings()
    calculate_pricing(
        subtotal=subtotal,
        promo=promo,
        loyalty_points=requested_points,
        point_value=settings.loyalty_point_value_rub,
        max_redeem_percent=settings.loyalty_max_redeem_percent,
    )

    if current_hold:
        current_hold.points = float(requested_points)
    else:
        current_hold = LoyaltyRedemptionHold(
            customer_id=customer_id,
            cart_id=cart.id,
            points=float(requested_points),
            status="reserved",
        )
        db.add(current_hold)

    return requested_points, current_hold


@router.post("/checkout", response_model=OrderOut)
def checkout(
    payload: CheckoutIn,
    response: Response,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
    customer: Customer = Depends(get_current_customer),
    db: Session = Depends(get_db),
):
    clean_key = _clean_idempotency_key(idempotency_key)
    fingerprint = _checkout_fingerprint(payload)

    try:
        existing = (
            db.query(CheckoutIdempotency)
            .filter(
                CheckoutIdempotency.customer_id == customer.id,
                CheckoutIdempotency.idempotency_key == clean_key,
            )
            .with_for_update()
            .first()
        )
        if existing:
            order = _validate_idempotency_record(db, existing, fingerprint, customer.id)
            response.headers["Idempotency-Replayed"] = "true"
            return order

        idempotency = CheckoutIdempotency(
            customer_id=customer.id,
            idempotency_key=clean_key,
            request_fingerprint=fingerprint,
        )
        db.add(idempotency)
        db.flush()

        cart = _load_locked_active_cart(db, customer.id)
        if not cart:
            raise HTTPException(status_code=409, detail="No active cart available for checkout")
        _validate_cart_for_checkout(cart)

        name = _clean_required(payload.name, "Name", 255)
        phone = _clean_required(payload.phone, "Phone", 64)
        delivery_type = _clean_required(payload.delivery_type, "Delivery type", 64).lower()
        address = (payload.address or "").strip()
        comment = (payload.comment or "").strip()[:2000]
        if delivery_type == "courier" and not address:
            raise HTTPException(status_code=400, detail="Address is required for courier delivery")
        if len(address) > 2000:
            raise HTTPException(status_code=400, detail="Address is too long")

        customer.first_name = name
        customer.phone = phone

        subtotal = money(
            sum(
                (money(item.product.price, "product price") * item.quantity for item in cart.items),
                Decimal("0.00"),
            ),
            "order subtotal",
        )
        if subtotal <= 0:
            raise HTTPException(status_code=409, detail="Order subtotal must be positive")

        promo = _lock_promo(db, cart)
        loyalty_points, loyalty_hold = _lock_and_validate_loyalty(
            db,
            cart,
            customer.id,
            subtotal,
            promo,
        )
        delivery_price = money(
            calculate_delivery_price(db, delivery_type, address),
            "delivery price",
        )
        settings = get_settings()
        pricing = calculate_pricing(
            subtotal=subtotal,
            promo=promo,
            loyalty_points=loyalty_points,
            point_value=settings.loyalty_point_value_rub,
            max_redeem_percent=settings.loyalty_max_redeem_percent,
            delivery_price=delivery_price,
            require_positive_total=True,
        )

        order = Order(
            customer_id=customer.id,
            promo_code_id=promo.id if promo else None,
            status="created",
            payment_status="pending",
            delivery_status="not_started",
            delivery_type=delivery_type,
            address=address,
            comment=comment,
            currency="RUB",
            discount_amount=float(pricing.promo_discount),
            loyalty_points_redeemed=float(pricing.loyalty_points),
            loyalty_discount_amount=float(pricing.loyalty_discount),
            referral_code=(cart.referral_code or "").strip().upper()[:64],
            delivery_price=float(pricing.delivery_price),
            total_amount=float(pricing.final_amount),
        )
        db.add(order)
        db.flush()

        for cart_item in sorted(cart.items, key=lambda item: item.variant_id):
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
                    price=float(money(product.price, "product price")),
                )
            )

        if promo:
            promo.used_count += 1
        if loyalty_hold:
            loyalty_hold.order_id = order.id
        cart.status = "converted"
        idempotency.order_id = order.id
        db.commit()

        response.headers["Idempotency-Replayed"] = "false"
        return _load_customer_order(db, order.id, customer.id)
    except HTTPException:
        db.rollback()
        raise
    except IntegrityError:
        db.rollback()
        existing = (
            db.query(CheckoutIdempotency)
            .filter(
                CheckoutIdempotency.customer_id == customer.id,
                CheckoutIdempotency.idempotency_key == clean_key,
            )
            .first()
        )
        if existing:
            order = _validate_idempotency_record(db, existing, fingerprint, customer.id)
            response.headers["Idempotency-Replayed"] = "true"
            return order
        raise HTTPException(status_code=409, detail="Checkout request conflicted with another update")
    except Exception:
        db.rollback()
        raise


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
        if order.payment_status in {"paid", "paid_review_required"}:
            raise HTTPException(status_code=409, detail="Paid order cannot be cancelled here; create refund")
        if order.status not in CANCELLABLE_ORDER_STATUSES:
            raise HTTPException(status_code=409, detail=f"Order in status {order.status} cannot be cancelled")

        order_with_items = (
            db.query(Order)
            .options(joinedload(Order.items))
            .filter(Order.id == order.id)
            .first()
        )
        for item in sorted(order_with_items.items, key=lambda order_item: order_item.variant_id):
            release_variant(db, item.variant_id, item.quantity)

        if order.promo_code_id:
            promo = (
                db.query(PromoCode)
                .filter(PromoCode.id == order.promo_code_id)
                .with_for_update()
                .first()
            )
            if promo:
                promo.used_count = max(promo.used_count - 1, 0)

        holds = (
            db.query(LoyaltyRedemptionHold)
            .filter(
                LoyaltyRedemptionHold.customer_id == customer.id,
                LoyaltyRedemptionHold.order_id == order.id,
                LoyaltyRedemptionHold.status == "reserved",
            )
            .with_for_update()
            .all()
        )
        for hold in holds:
            hold.status = "released"
            hold.released_at = datetime.utcnow()

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
