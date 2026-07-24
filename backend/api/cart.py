from decimal import Decimal, ROUND_HALF_UP

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..config import get_settings
from ..database import get_db
from ..models import Cart, CartItem, Customer, Product, ProductVariant, PromoCode
from ..schemas import (
    CartAddIn,
    CartItemOut,
    CartOut,
    CartQuantityIn,
    LoyaltyRedeemIn,
    PromoApplyIn,
    ReferralApplyIn,
)
from ..security import get_current_customer
from ..services.loyalty import attach_referral_to_customer, create_redemption_hold, redeem_points
from ..services.promos import calculate_discount

router = APIRouter(prefix="/cart", tags=["cart"])
settings = get_settings()
_MONEY = Decimal("0.01")


def _money(value: float | int | Decimal | None) -> float:
    return float(Decimal(str(value or 0)).quantize(_MONEY, rounding=ROUND_HALF_UP))


def _available_qty(variant: ProductVariant) -> int:
    stock = max(int(variant.stock_qty or 0), 0)
    reserved = max(int(variant.reserved_qty or 0), 0)
    return max(stock - reserved, 0)


def get_or_create_cart(db: Session, customer: Customer) -> Cart:
    cart = db.query(Cart).filter(Cart.customer_id == customer.id, Cart.status == "active").first()
    if not cart:
        cart = Cart(customer_id=customer.id, status="active")
        db.add(cart)
        db.commit()
        db.refresh(cart)
    return cart


def serialize_cart(cart: Cart) -> CartOut:
    items: list[CartItemOut] = []
    subtotal = Decimal("0")

    for item in cart.items:
        quantity = max(int(item.quantity or 0), 0)
        price = Decimal(str(item.product.price or 0))
        subtotal += price * quantity
        items.append(
            CartItemOut(
                id=item.id,
                product_id=item.product_id,
                variant_id=item.variant_id,
                title=item.product.title,
                size=item.variant.size,
                quantity=quantity,
                price=_money(price),
                available_qty=_available_qty(item.variant),
            )
        )

    subtotal_value = _money(subtotal)
    promo_discount = _money(calculate_discount(cart.promo_code, subtotal_value) if cart.promo_code else 0)
    points = max(float(cart.loyalty_points_to_redeem or 0), 0)
    loyalty_discount = _money(points * settings.loyalty_point_value_rub)
    discount_total = _money(min(promo_discount + loyalty_discount, subtotal_value))

    return CartOut(
        id=cart.id,
        items=items,
        total_amount=subtotal_value,
        discount_amount=discount_total,
        final_amount=_money(max(subtotal_value - discount_total, 0)),
        promo_code=cart.promo_code.code if cart.promo_code else None,
    )


@router.get("", response_model=CartOut)
def get_cart(customer: Customer = Depends(get_current_customer), db: Session = Depends(get_db)):
    return serialize_cart(get_or_create_cart(db, customer))


@router.post("/items", response_model=CartOut)
def add_item(payload: CartAddIn, customer: Customer = Depends(get_current_customer), db: Session = Depends(get_db)):
    cart = get_or_create_cart(db, customer)
    product = db.query(Product).filter(Product.id == payload.product_id, Product.active.is_(True)).first()
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")

    variant = (
        db.query(ProductVariant)
        .filter(ProductVariant.id == payload.variant_id, ProductVariant.product_id == product.id)
        .first()
    )
    if not variant:
        raise HTTPException(status_code=404, detail="Variant not found")

    existing = db.query(CartItem).filter(CartItem.cart_id == cart.id, CartItem.variant_id == variant.id).first()
    new_qty = payload.quantity + (existing.quantity if existing else 0)
    if new_qty > 10:
        raise HTTPException(status_code=422, detail="Maximum quantity per cart item is 10")
    if _available_qty(variant) < new_qty:
        raise HTTPException(status_code=409, detail="Not enough stock available")

    if existing:
        existing.quantity = new_qty
    else:
        db.add(CartItem(cart_id=cart.id, product_id=product.id, variant_id=variant.id, quantity=payload.quantity))
    db.commit()
    db.refresh(cart)
    return serialize_cart(cart)


@router.put("/items/{item_id}", response_model=CartOut)
def update_item_quantity(
    item_id: int,
    payload: CartQuantityIn,
    customer: Customer = Depends(get_current_customer),
    db: Session = Depends(get_db),
):
    cart = get_or_create_cart(db, customer)
    item = db.query(CartItem).filter(CartItem.id == item_id, CartItem.cart_id == cart.id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Cart item not found")

    if payload.quantity == 0:
        db.delete(item)
    else:
        if _available_qty(item.variant) < payload.quantity:
            raise HTTPException(status_code=409, detail="Not enough stock available")
        item.quantity = payload.quantity

    db.commit()
    db.refresh(cart)
    return serialize_cart(cart)


@router.delete("/items/{item_id}", response_model=CartOut)
def remove_item(item_id: int, customer: Customer = Depends(get_current_customer), db: Session = Depends(get_db)):
    cart = get_or_create_cart(db, customer)
    item = db.query(CartItem).filter(CartItem.id == item_id, CartItem.cart_id == cart.id).first()
    if item:
        db.delete(item)
        db.commit()
    db.refresh(cart)
    return serialize_cart(cart)


@router.post("/promo", response_model=CartOut)
def apply_promo(payload: PromoApplyIn, customer: Customer = Depends(get_current_customer), db: Session = Depends(get_db)):
    cart = get_or_create_cart(db, customer)
    promo = db.query(PromoCode).filter(PromoCode.code == payload.code.strip().upper()).first()
    if not promo:
        raise HTTPException(status_code=404, detail="Promo code not found")
    subtotal = _money(sum(Decimal(str(item.product.price or 0)) * max(int(item.quantity or 0), 0) for item in cart.items))
    calculate_discount(promo, subtotal)
    cart.promo_code_id = promo.id
    db.commit()
    db.refresh(cart)
    return serialize_cart(cart)


@router.delete("/promo", response_model=CartOut)
def remove_promo(customer: Customer = Depends(get_current_customer), db: Session = Depends(get_db)):
    cart = get_or_create_cart(db, customer)
    cart.promo_code_id = None
    db.commit()
    db.refresh(cart)
    return serialize_cart(cart)


@router.post("/loyalty", response_model=CartOut)
def apply_loyalty(payload: LoyaltyRedeemIn, customer: Customer = Depends(get_current_customer), db: Session = Depends(get_db)):
    cart = get_or_create_cart(db, customer)
    redeem_points(db, customer.id, cart, payload.points)
    create_redemption_hold(db, customer.id, cart.id, payload.points)
    db.commit()
    db.refresh(cart)
    return serialize_cart(cart)


@router.post("/referral", response_model=CartOut)
def apply_referral(payload: ReferralApplyIn, customer: Customer = Depends(get_current_customer), db: Session = Depends(get_db)):
    cart = get_or_create_cart(db, customer)
    normalized_code = payload.code.strip().upper()
    if attach_referral_to_customer(db, normalized_code, customer.id):
        cart.referral_code = normalized_code
    db.commit()
    db.refresh(cart)
    return serialize_cart(cart)
