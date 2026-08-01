from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from ..config import get_settings
from ..database import get_db
from ..models import Cart, CartItem, Customer, Product, ProductVariant, PromoCode
from ..schemas import CartAddIn, CartItemOut, CartOut, LoyaltyRedeemIn, PromoApplyIn, ReferralApplyIn
from ..security import get_current_customer
from ..services.cart_adjustments import (
    CartAdjustmentResult,
    apply_loyalty_request,
    reconcile_cart_adjustments,
)
from ..services.loyalty import attach_referral_to_customer
from ..services.promos import calculate_discount

router = APIRouter(prefix="/cart", tags=["cart"])

_MONEY_STEP = Decimal("0.01")
_MAX_VARIANT_QUANTITY = 10


def _money(value: object, field: str) -> Decimal:
    try:
        amount = Decimal(str(value)).quantize(_MONEY_STEP, rounding=ROUND_HALF_UP)
    except (InvalidOperation, TypeError, ValueError):
        raise HTTPException(status_code=409, detail=f"Invalid {field}")
    if not amount.is_finite():
        raise HTTPException(status_code=409, detail=f"Invalid {field}")
    return amount


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


def _load_cart(db: Session, cart_id: int) -> Cart:
    cart = (
        db.query(Cart)
        .options(
            joinedload(Cart.items).joinedload(CartItem.product),
            joinedload(Cart.items).joinedload(CartItem.variant),
            joinedload(Cart.promo_code),
        )
        .filter(Cart.id == cart_id)
        .first()
    )
    if not cart:
        raise HTTPException(status_code=404, detail="Cart not found")
    return cart


def _lock_cart(db: Session, cart_id: int) -> Cart:
    cart = db.query(Cart).filter(Cart.id == cart_id, Cart.status == "active").with_for_update().first()
    if not cart:
        raise HTTPException(status_code=409, detail="Active cart is no longer available")
    return cart


def _validate_item(item: CartItem) -> None:
    if not item.product or not item.variant or item.variant.product_id != item.product_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cart item {item.id} has a broken product/variant link",
        )
    if isinstance(item.quantity, bool) or not isinstance(item.quantity, int) or item.quantity <= 0:
        raise HTTPException(status_code=409, detail=f"Cart item {item.id} has invalid quantity")
    if item.quantity > _MAX_VARIANT_QUANTITY:
        raise HTTPException(status_code=409, detail=f"Cart item {item.id} exceeds quantity limit")
    if not item.product.active:
        raise HTTPException(status_code=409, detail=f"Product {item.product.title} is unavailable")
    if item.variant.stock_qty < 0 or item.variant.reserved_qty < 0 or item.variant.reserved_qty > item.variant.stock_qty:
        raise HTTPException(status_code=409, detail=f"Inventory state is invalid for cart item {item.id}")
    if item.variant.available_qty < item.quantity:
        raise HTTPException(status_code=409, detail=f"Not enough stock for cart item {item.id}")
    if _money(item.product.price, "product price") < 0:
        raise HTTPException(status_code=409, detail=f"Product {item.product.title} has invalid price")


def _merge_duplicate_carts(db: Session, carts: list[Cart]) -> Cart:
    primary = carts[0]
    for duplicate in carts[1:]:
        if duplicate.promo_code_id and primary.promo_code_id and duplicate.promo_code_id != primary.promo_code_id:
            raise HTTPException(status_code=409, detail="Duplicate carts have conflicting promo codes")
        if duplicate.referral_code and primary.referral_code and duplicate.referral_code != primary.referral_code:
            raise HTTPException(status_code=409, detail="Duplicate carts have conflicting referral codes")
        if (duplicate.loyalty_points_to_redeem or 0) > 0 or (primary.loyalty_points_to_redeem or 0) > 0:
            raise HTTPException(status_code=409, detail="Duplicate carts with loyalty holds require manual reconciliation")

        if not primary.promo_code_id:
            primary.promo_code_id = duplicate.promo_code_id
        if not primary.referral_code:
            primary.referral_code = duplicate.referral_code

        primary_by_variant = {item.variant_id: item for item in primary.items}
        for duplicate_item in list(duplicate.items):
            existing = primary_by_variant.get(duplicate_item.variant_id)
            if existing:
                combined_quantity = existing.quantity + duplicate_item.quantity
                if combined_quantity > _MAX_VARIANT_QUANTITY:
                    raise HTTPException(status_code=409, detail="Merged cart quantity exceeds limit")
                existing.quantity = combined_quantity
                db.delete(duplicate_item)
            else:
                duplicate_item.cart_id = primary.id
                primary.items.append(duplicate_item)
                primary_by_variant[duplicate_item.variant_id] = duplicate_item
        duplicate.status = "merged"
    return primary


def get_or_create_cart(db: Session, customer: Customer) -> Cart:
    try:
        locked_customer = (
            db.query(Customer)
            .filter(Customer.id == customer.id)
            .with_for_update()
            .first()
        )
        if not locked_customer:
            raise HTTPException(status_code=401, detail="Customer not found")

        cart_rows = (
            db.query(Cart)
            .filter(Cart.customer_id == customer.id, Cart.status == "active")
            .order_by(Cart.created_at.asc(), Cart.id.asc())
            .with_for_update()
            .all()
        )
        if cart_rows:
            carts = [_load_cart(db, row.id) for row in cart_rows]
            primary = _merge_duplicate_carts(db, carts) if len(carts) > 1 else carts[0]
            db.commit()
            return _load_cart(db, primary.id)

        cart = Cart(customer_id=customer.id, status="active")
        db.add(cart)
        db.commit()
        return _load_cart(db, cart.id)
    except HTTPException:
        db.rollback()
        raise
    except IntegrityError:
        db.rollback()
        cart = _active_cart_query(db, customer.id).order_by(Cart.created_at.asc(), Cart.id.asc()).first()
        if not cart:
            raise HTTPException(status_code=409, detail="Could not create active cart")
        return cart
    except Exception:
        db.rollback()
        raise


def serialize_cart(
    cart: Cart,
    adjustments: CartAdjustmentResult | None = None,
) -> CartOut:
    items: list[CartItemOut] = []
    subtotal = Decimal("0.00")
    for item in cart.items:
        _validate_item(item)
        price = _money(item.product.price, "product price")
        subtotal += price * item.quantity
        items.append(
            CartItemOut(
                id=item.id,
                product_id=item.product_id,
                variant_id=item.variant_id,
                title=item.product.title,
                size=item.variant.size,
                quantity=item.quantity,
                price=float(price),
                available_qty=item.variant.available_qty,
            )
        )

    subtotal = subtotal.quantize(_MONEY_STEP, rounding=ROUND_HALF_UP)
    if adjustments is None:
        discount = (
            _money(calculate_discount(cart.promo_code, float(subtotal)), "promo discount")
            if cart.promo_code
            else Decimal("0.00")
        )
        discount = min(max(discount, Decimal("0.00")), subtotal)
        loyalty_points = _money(cart.loyalty_points_to_redeem or 0, "loyalty points")
        point_value = _money(get_settings().loyalty_point_value_rub, "loyalty point value")
        loyalty_discount = (loyalty_points * point_value).quantize(_MONEY_STEP, rounding=ROUND_HALF_UP)
        loyalty_discount = min(max(loyalty_discount, Decimal("0.00")), subtotal - discount)
        final_amount = max(subtotal - discount - loyalty_discount, Decimal("0.00"))
        promo_code = cart.promo_code.code if cart.promo_code else None
    else:
        if subtotal != adjustments.subtotal:
            raise HTTPException(status_code=409, detail="Cart changed during adjustment reconciliation")
        discount = adjustments.promo_discount
        final_amount = adjustments.final_amount
        promo_code = adjustments.promo_code

    return CartOut(
        id=cart.id,
        items=items,
        total_amount=float(subtotal),
        discount_amount=float(discount),
        final_amount=float(final_amount),
        promo_code=promo_code,
    )


def _commit_reconciled_cart(db: Session, cart_id: int) -> CartOut:
    current_cart = _load_cart(db, cart_id)
    adjustments = reconcile_cart_adjustments(db, current_cart)
    db.commit()
    return serialize_cart(_load_cart(db, cart_id), adjustments)


@router.get("", response_model=CartOut)
def get_cart(customer: Customer = Depends(get_current_customer), db: Session = Depends(get_db)):
    cart = get_or_create_cart(db, customer)
    try:
        _lock_cart(db, cart.id)
        return _commit_reconciled_cart(db, cart.id)
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise


@router.post("/items", response_model=CartOut)
def add_item(payload: CartAddIn, customer: Customer = Depends(get_current_customer), db: Session = Depends(get_db)):
    cart = get_or_create_cart(db, customer)
    try:
        _lock_cart(db, cart.id)
        product = (
            db.query(Product)
            .filter(Product.id == payload.product_id, Product.active.is_(True))
            .first()
        )
        if not product:
            raise HTTPException(status_code=404, detail="Product not found")

        variant = (
            db.query(ProductVariant)
            .filter(ProductVariant.id == payload.variant_id, ProductVariant.product_id == product.id)
            .with_for_update()
            .first()
        )
        if not variant:
            raise HTTPException(status_code=404, detail="Variant not found for this product")
        if variant.stock_qty < 0 or variant.reserved_qty < 0 or variant.reserved_qty > variant.stock_qty:
            raise HTTPException(status_code=409, detail="Inventory state is invalid")

        existing = (
            db.query(CartItem)
            .filter(CartItem.cart_id == cart.id, CartItem.variant_id == variant.id)
            .with_for_update()
            .first()
        )
        new_quantity = payload.quantity + (existing.quantity if existing else 0)
        if new_quantity > _MAX_VARIANT_QUANTITY:
            raise HTTPException(status_code=409, detail="Quantity limit exceeded")
        if variant.available_qty < new_quantity:
            raise HTTPException(status_code=409, detail="Not enough stock available")

        if existing:
            existing.quantity = new_quantity
            existing.product_id = product.id
        else:
            db.add(
                CartItem(
                    cart_id=cart.id,
                    product_id=product.id,
                    variant_id=variant.id,
                    quantity=payload.quantity,
                )
            )
        return _commit_reconciled_cart(db, cart.id)
    except HTTPException:
        db.rollback()
        raise
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Duplicate or conflicting cart item") from exc
    except Exception:
        db.rollback()
        raise


@router.patch("/items/{item_id}", response_model=CartOut)
def update_item(
    item_id: int,
    quantity: int,
    customer: Customer = Depends(get_current_customer),
    db: Session = Depends(get_db),
):
    if quantity < 1 or quantity > _MAX_VARIANT_QUANTITY:
        raise HTTPException(status_code=422, detail="Quantity must be between 1 and 10")

    cart = get_or_create_cart(db, customer)
    try:
        _lock_cart(db, cart.id)
        item = (
            db.query(CartItem)
            .filter(CartItem.id == item_id, CartItem.cart_id == cart.id)
            .with_for_update()
            .first()
        )
        if not item:
            raise HTTPException(status_code=404, detail="Cart item not found")
        variant = (
            db.query(ProductVariant)
            .filter(ProductVariant.id == item.variant_id)
            .with_for_update()
            .first()
        )
        if not variant or variant.product_id != item.product_id:
            raise HTTPException(status_code=409, detail="Cart item variant is invalid")
        if variant.stock_qty < 0 or variant.reserved_qty < 0 or variant.reserved_qty > variant.stock_qty:
            raise HTTPException(status_code=409, detail="Inventory state is invalid")
        if variant.available_qty < quantity:
            raise HTTPException(status_code=409, detail="Not enough stock available")
        item.quantity = quantity
        return _commit_reconciled_cart(db, cart.id)
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise


@router.delete("/items/{item_id}", response_model=CartOut)
def remove_item(item_id: int, customer: Customer = Depends(get_current_customer), db: Session = Depends(get_db)):
    cart = get_or_create_cart(db, customer)
    try:
        _lock_cart(db, cart.id)
        item = (
            db.query(CartItem)
            .filter(CartItem.id == item_id, CartItem.cart_id == cart.id)
            .with_for_update()
            .first()
        )
        if not item:
            raise HTTPException(status_code=404, detail="Cart item not found")
        db.delete(item)
        return _commit_reconciled_cart(db, cart.id)
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise


@router.post("/promo", response_model=CartOut)
def apply_promo(payload: PromoApplyIn, customer: Customer = Depends(get_current_customer), db: Session = Depends(get_db)):
    cart = get_or_create_cart(db, customer)
    code = (payload.code or "").strip().upper()
    if not code:
        raise HTTPException(status_code=422, detail="Promo code is required")
    try:
        _lock_cart(db, cart.id)
        current_cart = _load_cart(db, cart.id)
        baseline = reconcile_cart_adjustments(db, current_cart)
        promo = db.query(PromoCode).filter(PromoCode.code == code).with_for_update().first()
        if not promo:
            raise HTTPException(status_code=404, detail="Promo code not found")
        calculate_discount(promo, float(baseline.subtotal))
        current_cart.promo_code_id = promo.id
        current_cart.promo_code = promo
        return _commit_reconciled_cart(db, cart.id)
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise


@router.delete("/promo", response_model=CartOut)
def remove_promo(customer: Customer = Depends(get_current_customer), db: Session = Depends(get_db)):
    cart = get_or_create_cart(db, customer)
    try:
        locked_cart = _lock_cart(db, cart.id)
        locked_cart.promo_code_id = None
        return _commit_reconciled_cart(db, cart.id)
    except Exception:
        db.rollback()
        raise


@router.post("/loyalty", response_model=CartOut)
def apply_loyalty(payload: LoyaltyRedeemIn, customer: Customer = Depends(get_current_customer), db: Session = Depends(get_db)):
    cart = get_or_create_cart(db, customer)
    try:
        _lock_cart(db, cart.id)
        current_cart = _load_cart(db, cart.id)
        adjustments = apply_loyalty_request(db, current_cart, payload.points)
        db.commit()
        return serialize_cart(_load_cart(db, cart.id), adjustments)
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise


@router.post("/referral", response_model=CartOut)
def apply_referral(payload: ReferralApplyIn, customer: Customer = Depends(get_current_customer), db: Session = Depends(get_db)):
    cart = get_or_create_cart(db, customer)
    code = (payload.code or "").strip().upper()
    if not code:
        raise HTTPException(status_code=422, detail="Referral code is required")
    try:
        locked_cart = _lock_cart(db, cart.id)
        if not attach_referral_to_customer(db, code, customer.id):
            raise HTTPException(status_code=404, detail="Referral code not found or unavailable")
        locked_cart.referral_code = code[:64]
        return _commit_reconciled_cart(db, cart.id)
    except HTTPException:
        db.rollback()
        raise
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Referral attribution already exists") from exc
    except Exception:
        db.rollback()
        raise
