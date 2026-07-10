from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from ..database import get_db
from ..models import Cart, CartItem, Customer, Product, ProductVariant, PromoCode
from ..schemas import CartAddIn, CartItemOut, CartOut, PromoApplyIn, LoyaltyRedeemIn, ReferralApplyIn
from ..security import get_current_customer
from ..services.promos import calculate_discount
from ..services.loyalty import redeem_points, attach_referral_to_customer, create_redemption_hold

router = APIRouter(prefix="/cart", tags=["cart"])


def get_or_create_cart(db: Session, customer: Customer) -> Cart:
    cart = db.query(Cart).filter(Cart.customer_id == customer.id, Cart.status == "active").first()
    if not cart:
        cart = Cart(customer_id=customer.id, status="active")
        db.add(cart)
        db.commit()
        db.refresh(cart)
    return cart


def serialize_cart(cart: Cart) -> CartOut:
    items = []
    subtotal = 0.0
    for item in cart.items:
        price = item.product.price
        subtotal += price * item.quantity
        items.append(CartItemOut(
            id=item.id,
            product_id=item.product_id,
            variant_id=item.variant_id,
            title=item.product.title,
            size=item.variant.size,
            quantity=item.quantity,
            price=price,
            available_qty=item.variant.available_qty,
        ))
    discount = calculate_discount(cart.promo_code, subtotal) if cart.promo_code else 0
    loyalty_discount = cart.loyalty_points_to_redeem
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
    cart = get_or_create_cart(db, customer)
    return serialize_cart(cart)


@router.post("/items", response_model=CartOut)
def add_item(payload: CartAddIn, customer: Customer = Depends(get_current_customer), db: Session = Depends(get_db)):
    cart = get_or_create_cart(db, customer)
    product = db.query(Product).filter(Product.id == payload.product_id, Product.active == True).first()
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    variant = db.query(ProductVariant).filter(ProductVariant.id == payload.variant_id, ProductVariant.product_id == product.id).first()
    if not variant:
        raise HTTPException(status_code=404, detail="Variant not found")
    existing = db.query(CartItem).filter(CartItem.cart_id == cart.id, CartItem.variant_id == variant.id).first()
    new_qty = payload.quantity + (existing.quantity if existing else 0)
    if variant.available_qty < new_qty:
        raise HTTPException(status_code=409, detail="Not enough stock available")
    if existing:
        existing.quantity = new_qty
    else:
        db.add(CartItem(cart_id=cart.id, product_id=product.id, variant_id=variant.id, quantity=payload.quantity))
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
    subtotal = sum(item.product.price * item.quantity for item in cart.items)
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
    if attach_referral_to_customer(db, payload.code, customer.id):
        cart.referral_code = payload.code.strip().upper()
    db.commit()
    db.refresh(cart)
    return serialize_cart(cart)
