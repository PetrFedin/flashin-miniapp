import csv
import io
import math

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from ..database import get_db
from ..models import (
    AdminUser,
    AuditLog,
    CrmProfile,
    Customer,
    MoySkladConflict,
    MoySkladMappingRule,
    Notification,
    Order,
    Product,
    ProductImage,
    ProductVariant,
    PromoCode,
)
from ..order_statuses import ADMIN_MANAGED_ORDER_TRANSITIONS
from ..schemas import (
    AdminLoginIn,
    AdminProductUpdate,
    AuditLogOut,
    MoySkladConflictOut,
    MoySkladMappingRuleCreate,
    MoySkladMappingRuleOut,
    OrderOut,
    OrderStatusUpdate,
    ProductCreate,
    ProductOut,
    PromoCodeCreate,
    TokenOut,
)
from ..security import (
    create_admin_token,
    get_current_admin,
    hash_password,
    password_needs_rehash,
    verify_password,
)
from ..services.audit import log_admin_action
from ..services.inventory import adjust_stock
from ..services.notifications import queue_order_status
from ..services.rbac import require_permission

router = APIRouter(prefix="/admin", tags=["admin"])

_MAX_CSV_BYTES = 5 * 1024 * 1024
_MAX_CSV_ROWS = 10_000
_DELIVERY_STATUSES = {
    "not_started",
    "assembling",
    "ready",
    "shipped",
    "delivered",
    "cancelled",
}


def _clean(value: object, field: str, max_length: int, required: bool = True) -> str:
    cleaned = str(value or "").strip()
    if required and not cleaned:
        raise HTTPException(status_code=400, detail=f"{field} is required")
    if len(cleaned) > max_length:
        raise HTTPException(status_code=400, detail=f"{field} is too long")
    return cleaned


def _positive_price(value: object) -> float:
    try:
        price = float(value)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Price must be numeric")
    if not math.isfinite(price) or price <= 0:
        raise HTTPException(status_code=400, detail="Price must be positive")
    return round(price, 2)


def _stock_quantity(value: object) -> int:
    if isinstance(value, bool):
        raise HTTPException(status_code=400, detail="Stock quantity must be a non-negative integer")
    try:
        quantity = float(value)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Stock quantity must be a non-negative integer")
    if not math.isfinite(quantity) or quantity < 0 or not quantity.is_integer():
        raise HTTPException(status_code=400, detail="Stock quantity must be a non-negative integer")
    return int(quantity)


def _order_with_items(db: Session, order_id: int) -> Order | None:
    return (
        db.query(Order)
        .options(joinedload(Order.items), joinedload(Order.customer))
        .filter(Order.id == order_id)
        .first()
    )


@router.post("/login", response_model=TokenOut)
def admin_login(payload: AdminLoginIn, db: Session = Depends(get_db)):
    email = _clean(payload.email, "Email", 255).lower()
    admin = (
        db.query(AdminUser)
        .filter(AdminUser.email == email, AdminUser.active.is_(True))
        .with_for_update()
        .first()
    )
    if not admin or not verify_password(payload.password, admin.password_hash):
        db.rollback()
        raise HTTPException(status_code=401, detail="Invalid admin credentials")
    if password_needs_rehash(admin.password_hash):
        admin.password_hash = hash_password(payload.password)
        db.commit()
    else:
        db.rollback()
    return TokenOut(access_token=create_admin_token(admin.id, admin.role))


@router.get("/products", response_model=list[ProductOut])
def admin_products(admin=Depends(get_current_admin), db: Session = Depends(get_db)):
    require_permission(db, admin, "products.read")
    return (
        db.query(Product)
        .options(joinedload(Product.images), joinedload(Product.variants))
        .order_by(Product.created_at.desc())
        .all()
    )


@router.post("/products", response_model=ProductOut)
def admin_create_product(
    payload: ProductCreate,
    admin=Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    require_permission(db, admin, "products.write")
    initial_stocks = [_stock_quantity(raw_variant.get("stock_qty", 0)) for raw_variant in payload.variants]
    if any(stock_qty > 0 for stock_qty in initial_stocks):
        require_permission(db, admin, "inventory.write")
    sku = _clean(payload.sku, "SKU", 120).upper()
    slug = _clean(payload.slug, "Slug", 255).lower()
    title = _clean(payload.title, "Title", 255)
    variant_skus: set[str] = set()

    try:
        product = Product(
            sku=sku,
            title=title,
            slug=slug,
            brand=_clean(payload.brand, "Brand", 120),
            description=(payload.description or "").strip(),
            price=_positive_price(payload.price),
            old_price=payload.old_price,
            currency=_clean(payload.currency, "Currency", 8).upper(),
            category=_clean(payload.category, "Category", 120),
            gender=_clean(payload.gender, "Gender", 32),
            active=payload.active,
            is_drop=payload.is_drop,
            is_rare=payload.is_rare,
            drop_starts_at=payload.drop_starts_at,
            vip_only_until=payload.vip_only_until,
        )
        db.add(product)
        db.flush()

        for index, url in enumerate(payload.images):
            cleaned_url = _clean(url, "Image URL", 2048)
            db.add(ProductImage(product_id=product.id, url=cleaned_url, sort_order=index))

        for index, raw_variant in enumerate(payload.variants):
            variant_sku = _clean(raw_variant.get("sku"), "Variant SKU", 120).upper()
            if variant_sku in variant_skus:
                raise HTTPException(status_code=409, detail=f"Duplicate variant SKU: {variant_sku}")
            variant_skus.add(variant_sku)
            variant = ProductVariant(
                product_id=product.id,
                size=_clean(raw_variant.get("size"), "Variant size", 32),
                color=_clean(raw_variant.get("color", ""), "Variant color", 64, required=False),
                sku=variant_sku,
                stock_qty=0,
                reserved_qty=0,
            )
            db.add(variant)
            db.flush()
            if initial_stocks[index] > 0:
                adjust_stock(
                    db,
                    variant.id,
                    initial_stocks[index],
                    reason="Product creation",
                    admin_id=admin.id,
                )

        log_admin_action(db, admin, "product.create", "product", product.id, {"sku": sku})
        db.commit()
    except HTTPException:
        db.rollback()
        raise
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Product or variant already exists") from exc
    except Exception:
        db.rollback()
        raise

    return (
        db.query(Product)
        .options(joinedload(Product.images), joinedload(Product.variants))
        .filter(Product.id == product.id)
        .first()
    )


@router.patch("/products/{product_id}/active", response_model=ProductOut)
def admin_toggle_product(
    product_id: int,
    active: bool,
    admin=Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    require_permission(db, admin, "products.write")
    product = db.query(Product).filter(Product.id == product_id).with_for_update().first()
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    product.active = active
    log_admin_action(db, admin, "product.active", "product", product.id, {"active": active})
    db.commit()
    return (
        db.query(Product)
        .options(joinedload(Product.images), joinedload(Product.variants))
        .filter(Product.id == product.id)
        .first()
    )


@router.patch("/variants/{variant_id}/stock")
def admin_update_stock(
    variant_id: int,
    stock_qty: int,
    reason: str = "manual update",
    admin=Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    require_permission(db, admin, "inventory.write")
    try:
        variant = adjust_stock(
            db,
            variant_id,
            stock_qty,
            reason=reason,
            admin_id=admin.id,
        )
        log_admin_action(
            db,
            admin,
            "inventory.adjust",
            "variant",
            variant.id,
            {"stock_qty": stock_qty, "reason": reason},
        )
        db.commit()
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise
    return {
        "ok": True,
        "variant_id": variant.id,
        "stock_qty": variant.stock_qty,
        "reserved_qty": variant.reserved_qty,
    }


@router.get("/orders", response_model=list[OrderOut])
def admin_orders(admin=Depends(get_current_admin), db: Session = Depends(get_db)):
    require_permission(db, admin, "orders.read")
    return db.query(Order).options(joinedload(Order.items)).order_by(Order.created_at.desc()).all()


@router.patch("/orders/{order_id}", response_model=OrderOut)
def admin_update_order(
    order_id: int,
    payload: OrderStatusUpdate,
    admin=Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    require_permission(db, admin, "orders.write")
    try:
        order = db.query(Order).filter(Order.id == order_id).with_for_update().first()
        if not order:
            raise HTTPException(status_code=404, detail="Order not found")
        previous_status = order.status
        changed: dict[str, object] = {}

        requested_status = (payload.status or "").strip().lower()
        if requested_status and requested_status != order.status:
            allowed_targets = ADMIN_MANAGED_ORDER_TRANSITIONS.get(order.status, frozenset())
            if requested_status not in allowed_targets:
                raise HTTPException(
                    status_code=409,
                    detail=f"Transition {order.status} -> {requested_status} is not allowed",
                )
            order.status = requested_status
            changed["status"] = requested_status

        if payload.delivery_status:
            normalized_delivery_status = payload.delivery_status.strip().lower()
            if normalized_delivery_status not in _DELIVERY_STATUSES:
                raise HTTPException(status_code=400, detail="Invalid delivery status")
            if normalized_delivery_status != order.delivery_status:
                order.delivery_status = normalized_delivery_status
                changed["delivery_status"] = normalized_delivery_status

        if payload.tracking_number is not None:
            tracking_number = _clean(
                payload.tracking_number,
                "Tracking number",
                255,
                required=False,
            )
            if tracking_number != order.tracking_number:
                order.tracking_number = tracking_number
                changed["tracking_number"] = tracking_number

        if changed:
            queue_order_status(db, order)
            log_admin_action(
                db,
                admin,
                "order.update",
                "order",
                order.id,
                {"from_status": previous_status, **changed},
            )
            db.commit()
        else:
            db.rollback()
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise

    return _order_with_items(db, order_id)


@router.post("/promocodes")
def admin_create_promo(
    payload: PromoCodeCreate,
    admin=Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    require_permission(db, admin, "promo.write")
    code = _clean(payload.code, "Promo code", 64).upper()
    if payload.discount_value < 0 or not math.isfinite(payload.discount_value):
        raise HTTPException(status_code=400, detail="Discount value must be non-negative")
    if payload.min_amount < 0 or not math.isfinite(payload.min_amount):
        raise HTTPException(status_code=400, detail="Minimum amount must be non-negative")
    if payload.max_uses < 0:
        raise HTTPException(status_code=400, detail="Maximum uses must be non-negative")

    try:
        promo = PromoCode(**payload.model_dump())
        promo.code = code
        db.add(promo)
        db.flush()
        log_admin_action(db, admin, "promocode.create", "promocode", promo.id, {"code": code})
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Promo code already exists") from exc
    except Exception:
        db.rollback()
        raise
    return {"ok": True, "id": promo.id}


@router.get("/notifications")
def admin_notifications(admin=Depends(get_current_admin), db: Session = Depends(get_db)):
    require_permission(db, admin, "support.write")
    return db.query(Notification).order_by(Notification.created_at.desc()).limit(100).all()


@router.post("/products/import-csv")
async def admin_import_products_csv(
    file: UploadFile = File(...),
    admin=Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    require_permission(db, admin, "products.write")
    require_permission(db, admin, "inventory.write")
    raw = await file.read(_MAX_CSV_BYTES + 1)
    if len(raw) > _MAX_CSV_BYTES:
        raise HTTPException(status_code=413, detail="CSV file is too large")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise HTTPException(status_code=400, detail="CSV must be UTF-8 encoded")

    reader = csv.DictReader(io.StringIO(text))
    required = {"sku", "title", "slug", "price", "size", "variant_sku", "stock_qty"}
    if not required.issubset(set(reader.fieldnames or [])):
        raise HTTPException(status_code=400, detail=f"CSV must contain columns: {sorted(required)}")

    created_products = 0
    updated_variants = 0
    try:
        for row_number, row in enumerate(reader, start=2):
            if row_number > _MAX_CSV_ROWS + 1:
                raise HTTPException(status_code=413, detail="CSV row limit exceeded")
            sku = _clean(row.get("sku"), f"SKU at row {row_number}", 120).upper()
            product = db.query(Product).filter(Product.sku == sku).with_for_update().first()
            price = _positive_price(row.get("price"))
            if not product:
                product = Product(
                    sku=sku,
                    title=_clean(row.get("title"), f"Title at row {row_number}", 255),
                    slug=_clean(row.get("slug"), f"Slug at row {row_number}", 255).lower(),
                    brand=_clean(row.get("brand") or "FLASHIN", "Brand", 120),
                    description=(row.get("description") or "").strip(),
                    price=price,
                    currency=_clean(row.get("currency") or "RUB", "Currency", 8).upper(),
                    category=_clean(row.get("category") or "Clothing", "Category", 120),
                    gender=_clean(row.get("gender") or "unisex", "Gender", 32),
                    active=str(row.get("active", "true")).strip().lower() != "false",
                )
                db.add(product)
                db.flush()
                created_products += 1
                if row.get("image_url"):
                    db.add(
                        ProductImage(
                            product_id=product.id,
                            url=_clean(row.get("image_url"), "Image URL", 2048),
                            sort_order=0,
                        )
                    )
            else:
                product.title = _clean(row.get("title"), f"Title at row {row_number}", 255)
                product.price = price
                product.active = str(row.get("active", "true")).strip().lower() != "false"

            variant_sku = _clean(row.get("variant_sku"), f"Variant SKU at row {row_number}", 120).upper()
            stock_qty = _stock_quantity(row.get("stock_qty"))
            variant = (
                db.query(ProductVariant)
                .filter(ProductVariant.sku == variant_sku)
                .with_for_update()
                .first()
            )
            if not variant:
                variant = ProductVariant(
                    product_id=product.id,
                    size=_clean(row.get("size"), f"Size at row {row_number}", 32),
                    color=_clean(row.get("color", ""), "Color", 64, required=False),
                    sku=variant_sku,
                    stock_qty=0,
                    reserved_qty=0,
                )
                db.add(variant)
                db.flush()
                if stock_qty > 0:
                    adjust_stock(
                        db,
                        variant.id,
                        stock_qty,
                        reason="CSV import",
                        admin_id=admin.id,
                    )
            else:
                if variant.product_id != product.id:
                    raise HTTPException(
                        status_code=409,
                        detail=f"Variant SKU {variant_sku} belongs to another product",
                    )
                adjust_stock(
                    db,
                    variant.id,
                    stock_qty,
                    reason="CSV import",
                    admin_id=admin.id,
                )
                variant.color = _clean(
                    row.get("color", variant.color),
                    "Color",
                    64,
                    required=False,
                )
            updated_variants += 1

        log_admin_action(
            db,
            admin,
            "products.import_csv",
            "product",
            "",
            {"created_products": created_products, "updated_variants": updated_variants},
        )
        db.commit()
    except HTTPException:
        db.rollback()
        raise
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="CSV contains duplicate product data") from exc
    except Exception:
        db.rollback()
        raise

    return {
        "ok": True,
        "created_products": created_products,
        "updated_variants": updated_variants,
    }


@router.get("/orders/export-csv")
def admin_export_orders_csv(admin=Depends(get_current_admin), db: Session = Depends(get_db)):
    require_permission(db, admin, "orders.read")
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(
        [
            "order_id",
            "created_at",
            "status",
            "payment_status",
            "delivery_status",
            "total_amount",
            "currency",
            "customer_id",
            "delivery_type",
            "address",
            "tracking_number",
        ]
    )
    for order in db.query(Order).order_by(Order.created_at.desc()).all():
        writer.writerow(
            [
                order.id,
                order.created_at.isoformat(),
                order.status,
                order.payment_status,
                order.delivery_status,
                order.total_amount,
                order.currency,
                order.customer_id,
                order.delivery_type,
                order.address,
                order.tracking_number,
            ]
        )
    return Response(
        content=output.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=flashin_orders.csv"},
    )


@router.get("/audit-logs", response_model=list[AuditLogOut])
def admin_audit_logs(admin=Depends(get_current_admin), db: Session = Depends(get_db)):
    require_permission(db, admin, "audit.read")
    return db.query(AuditLog).order_by(AuditLog.created_at.desc()).limit(200).all()


@router.get("/products/{product_id}", response_model=ProductOut)
def admin_product_detail(
    product_id: int,
    admin=Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    require_permission(db, admin, "products.read")
    product = (
        db.query(Product)
        .options(joinedload(Product.images), joinedload(Product.variants))
        .filter(Product.id == product_id)
        .first()
    )
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    return product


@router.patch("/products/{product_id}", response_model=ProductOut)
def admin_product_update(
    product_id: int,
    payload: AdminProductUpdate,
    admin=Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    require_permission(db, admin, "products.write")
    try:
        product = db.query(Product).filter(Product.id == product_id).with_for_update().first()
        if not product:
            raise HTTPException(status_code=404, detail="Product not found")
        data = payload.model_dump(exclude_unset=True)
        if "price" in data and data["price"] is not None:
            data["price"] = _positive_price(data["price"])
        for key, value in data.items():
            if isinstance(value, str):
                value = value.strip()
            setattr(product, key, value)
        log_admin_action(db, admin, "product.update", "product", product.id, data)
        db.commit()
    except HTTPException:
        db.rollback()
        raise
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Product update conflicts with existing data") from exc
    except Exception:
        db.rollback()
        raise
    return (
        db.query(Product)
        .options(joinedload(Product.images), joinedload(Product.variants))
        .filter(Product.id == product.id)
        .first()
    )


@router.get("/customers")
def admin_customers(admin=Depends(get_current_admin), db: Session = Depends(get_db)):
    require_permission(db, admin, "customers.read")
    rows = db.query(Customer).order_by(Customer.created_at.desc()).limit(500).all()
    return [
        {
            "id": customer.id,
            "telegram_id": customer.telegram_id,
            "username": customer.username,
            "first_name": customer.first_name,
            "phone": customer.phone,
        }
        for customer in rows
    ]


@router.get("/customers/{customer_id}")
def admin_customer_detail(
    customer_id: int,
    admin=Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    require_permission(db, admin, "customers.read")
    customer = db.query(Customer).filter(Customer.id == customer_id).first()
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")
    profile = db.query(CrmProfile).filter(CrmProfile.customer_id == customer.id).first()
    return {
        "customer": {
            "id": customer.id,
            "telegram_id": customer.telegram_id,
            "username": customer.username,
            "first_name": customer.first_name,
            "phone": customer.phone,
        },
        "profile": profile,
    }


@router.post("/moysklad/mapping-rules", response_model=MoySkladMappingRuleOut)
def create_mapping_rule(
    payload: MoySkladMappingRuleCreate,
    admin=Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    require_permission(db, admin, "products.write")
    rule = MoySkladMappingRule(**payload.model_dump())
    db.add(rule)
    db.flush()
    log_admin_action(
        db,
        admin,
        "moysklad.mapping.create",
        "moysklad_mapping_rule",
        rule.id,
        payload.model_dump(),
    )
    db.commit()
    db.refresh(rule)
    return rule


@router.get("/moysklad/mapping-rules", response_model=list[MoySkladMappingRuleOut])
def list_mapping_rules(admin=Depends(get_current_admin), db: Session = Depends(get_db)):
    require_permission(db, admin, "products.read")
    return db.query(MoySkladMappingRule).order_by(MoySkladMappingRule.id.desc()).all()


@router.get("/moysklad/conflicts", response_model=list[MoySkladConflictOut])
def list_moysklad_conflicts(admin=Depends(get_current_admin), db: Session = Depends(get_db)):
    require_permission(db, admin, "products.read")
    return db.query(MoySkladConflict).order_by(MoySkladConflict.created_at.desc()).limit(200).all()
