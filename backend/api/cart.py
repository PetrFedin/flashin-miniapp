from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from ..database import get_db
from ..models import Cart, CartItem, Customer, Product, ProductVariant, PromoCode
from ..schemas import CartAddIn, CartItemOut, CartOut, LoyaltyRedeemIn, PromoApplyIn, ReferralApplyIn
from ..security import get_current_customer
from ..services.loyalty import attach_referral_to_customer, create_redemption_hold, redeem_points
from ..services.promos import calculate_discount

router = APIRouter(prefix="/cart", tags=["cart"])


def _active_cart_query(db: Session, customer_id: int):
    return (
        db.query(Cart)
        .options(
            joinedload(Cart.items).joinedload(CartItem.product),
            joinedload(Cart.items).joinedload(CartItem.variant),
            joinedload(Cart.promo_code),
        )
        .filter(Cart.customer_id == customer_id, Cart.status == "active")
    )


def get_or_create_cart(db: Session, customer: Customer) -> Cart:
    carts = _active_cart_query(db, customer.id).order_by(Cart.created_at.asc()).all()
    if carts:
        primary = carts[0]
        # Heal historical duplicate active carts instead of returning an arbitrary record.
        for duplicate in carts[1:]:
            for duplicate_item in list(duplicate.items):
                existing = next((item for item in primary.items if item.variant_id == duplicate_item.variant_id), None)
                if existing:
                    existing.quantity += duplicate_item.quantity
                    db.delete(duplicate_item)
                else:
                    duplicate_item.cart_id = primary.id
            duplicate.status = "merged"
        if len(carts) > 1:
            db.commit()
            return _active_cart_query(db, customer.id).filter(Cart.id == primary.id).first()
        return primary

    cart = Cart(customer_id=customer.id, status="active")
    try:
        db.add(cart)
        db.commit()
        db.refresh(cart)
    except IntegrityError:
        db.rollback()
        cart = _active_cart_query(db, customer.id).first()
        if not cart:
            raise
    return cart


def serialize_cart(cart: Cart) -> CartOut:
    items: list[CartItemOut] = []
    subtotal = 0.0
    for item in cart.items:
        if not item.product or not item.variant or item.variant.product_id != item.product_id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Cart item {item.id} has a broken product/variant link",
            )
        if item.quantity <= 0:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"Cart item {item.id} has invalid quantity")
        price = item.product.price
        subtotal += price * item.quantity
        items.append(
            CartItemOut(
                id=item.id,
                product_id=item.product_id,
                variant_id=item.variant_id,
                title=item.product.title,
                size=item.variant.size,
                quantity=item.quantity,
                price=price,
                available_qty=item.variant.available_qty,
            )
        )
    discount = calculate_discount(cart.promo_code, subtotal) if cart.promo_code else 0
    loyalty_discount = min(max(cart.loyalty_points_to_redeem, 0), max(subtotal - discount, 0))
    return CartOut(
        id=cart.id,
        items=items,
        total_amount=subtotal,
        discount_amount=discount,
        final_amount=max(subtotal - discount - loyalty_discount, 0),
        promo_code=cart.promo_code.code if cart.promo_code else None,
    )


@router.get("", response_model=CartOut)
def get_cart(customer: Customer = Depends(get_current_customer), db: Session = Depends(get_db)):
    return serialize_cart(get_or_create_cart(db, customer))


@router.post("/items", response_model=CartOut)
def add_item(payload: CartAddIn, customer: Customer = Depends(get_current_customer), db: Session = Depends(get_db)):
    if payload.quantity <= 0:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Quantity must be greater than zero")

    cart = get_or_create_cart(db, customer)
    product = db.query(Product).filter(Product.id == payload.product_id, Product.active.is_(True)).first()
    if not product:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product not found")
    variant = (
        db.query(ProductVariant)
        .filter(ProductVariant.id == payload.variant_id, ProductVariant.product_id == product.id)
        .first()
    )
    if not variant:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Variant not found for this product")

    existing = db.query(CartItem).filter(CartItem.cart_id == cart.id, CartItem.variant_id == variant.id).first()
    new_qty = payload.quantity + (existing.quantity if existing else 0)
    if variant.available_qty < new_qty:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Not enough stock available")

    try:
        if existing:
            existing.quantity = new_qty
            existing.product_id = product.id
        else:
            db.add(CartItem(cart_id=cart.id, product_id=product.id, variant_id=variant.id, quantity=payload.quantity))
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Duplicate or conflicting cart item") from exc
    except Exception:
        db.rollback()
        raise

    return serialize_cart(_active_cart_query(db, customer.id).filter(Cart.id == cart.id).first())


@router.delete("/items/{item_id}", response_model=CartOut)
def remove_item(item_id: int, customer: Customer = Depends(get_current_customer), db: Session = Depends(get_db)):
    cart = get_or_create_cart(db, customer)
    item = db.query(CartItem).filter(CartItem.id == item_id, CartItem.cart_id == cart.id).first()
    if not item:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cart item not found")
    db.delete(item)
    db.commit()
    return serialize_cart(_active_cart_query(db, customer.id).filter(Cart.id == cart.id).first())


@router.post("/promo", response_model=CartOut)
def apply_promo(payload: PromoApplyIn, customer: Customer = Depends(get_current_customer), db: Session = Depends(get_db)):
    cart = get_or_create_cart(db, customer)
    code = payload.code.strip().upper()
    if not code:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Promo code is required")
    promo = db.query(PromoCode).filter(PromoCode.code == code).first()
    if not promo:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Promo code not found")
    subtotal = sum(item.product.price * item.quantity for item in cart.items)
    calculate_discount(promo, subtotal)
    cart.promo_code_id = promo.id
    db.commit()
    return serialize_cart(_active_cart_query(db, customer.id).filter(Cart.id == cart.id).first())


@router.delete("/promo", response_model=CartOut)
def remove_promo(customer: Customer = Depends(get_current_customer), db: Session = Depends(get_db)):
    cart = get_or_create_cart(db, customer)
    cart.promo_code_id = None
    db.commit()
    return serialize_cart(_active_cart_query(db, customer.id).filter(Cart.id == cart.id).first())


@router.post("/loyalty", response_model=CartOut)
def apply_loyalty(payload: LoyaltyRedeemIn, customer: Customer = Depends(get_current_customer), db: Session = Depends(get_db)):
    if payload.points <= 0:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Points must be greater than zero")
    cart = get_or_create_cart(db, customer)
    try:
        redeem_points(db, customer.id, cart, payload.points)
        create_redemption_hold(db, customer.id, cart.id, payload.points)
        db.commit()
    except Exception:
        db.rollback()
        raise
    return serialize_cart(_active_cart_query(db, customer.id).filter(Cart.id == cart.id).first())


@router.post("/referral", response_model=CartOut)
def apply_referral(payload: ReferralApplyIn, customer: Customer = Depends(get_current_customer), db: Session = Depends(get_db)):
    cart = get_or_create_cart(db, customer)
    code = payload.code.strip().upper()
    if not code:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Referral code is required")
    if not attach_referral_to_customer(db, code, customer.id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Referral code not found or unavailable")
    cart.referral_code = code
    db.commit()
    return serialize_cart(_active_cart_query(db, customer.id).filter(Cart.id == cart.id).first())
