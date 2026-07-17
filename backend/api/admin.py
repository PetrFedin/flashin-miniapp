import csv
import io
from fastapi import APIRouter, Depends, HTTPException, File, UploadFile, Response
from sqlalchemy.orm import Session, joinedload
from ..database import get_db
from ..models import AdminUser, Notification, Order, Product, ProductImage, ProductVariant, PromoCode
from ..schemas import AdminLoginIn, OrderOut, OrderStatusUpdate, ProductCreate, ProductOut, PromoCodeCreate, TokenOut, InventoryAdjustmentIn, AuditLogOut, MoySkladMappingRuleCreate, MoySkladMappingRuleOut, MoySkladConflictOut, AdminProductUpdate
from ..security import create_admin_token, get_current_admin, hash_password, verify_password
from ..services.notifications import queue_order_status
from ..services.inventory import release_variant, adjust_stock
from ..services.audit import log_admin_action
from ..services.rbac import require_permission

router = APIRouter(prefix="/admin", tags=["admin"])


@router.post("/login", response_model=TokenOut)
def admin_login(payload: AdminLoginIn, db: Session = Depends(get_db)):
    admin = db.query(AdminUser).filter(AdminUser.email == payload.email, AdminUser.active == True).first()
    if not admin or not verify_password(payload.password, admin.password_hash):
        raise HTTPException(status_code=401, detail="Invalid admin credentials")
    return TokenOut(access_token=create_admin_token(admin.id, admin.role))


@router.get("/products", response_model=list[ProductOut])
def admin_products(admin=Depends(get_current_admin), db: Session = Depends(get_db)):
    return db.query(Product).options(joinedload(Product.images), joinedload(Product.variants)).order_by(Product.created_at.desc()).all()


@router.post("/products", response_model=ProductOut)
def admin_create_product(payload: ProductCreate, admin=Depends(get_current_admin), db: Session = Depends(get_db)):
    require_permission(db, admin, "products.write")
    product = Product(
        sku=payload.sku,
        title=payload.title,
        slug=payload.slug,
        brand=payload.brand,
        description=payload.description,
        price=payload.price,
        old_price=payload.old_price,
        currency=payload.currency,
        category=payload.category,
        gender=payload.gender,
        active=payload.active,
        is_drop=payload.is_drop,
        is_rare=payload.is_rare,
        drop_starts_at=payload.drop_starts_at,
        vip_only_until=payload.vip_only_until,
    )
    db.add(product)
    db.flush()
    for idx, url in enumerate(payload.images):
        db.add(ProductImage(product_id=product.id, url=url, sort_order=idx))
    for v in payload.variants:
        db.add(ProductVariant(
            product_id=product.id,
            size=v.size,
            color=v.color,
            sku=v.sku,
            stock_qty=v.stock_qty,
            reserved_qty=0,
        ))
    db.commit()
    return db.query(Product).options(joinedload(Product.images), joinedload(Product.variants)).filter(Product.id == product.id).first()


@router.patch("/products/{product_id}/active", response_model=ProductOut)
def admin_toggle_product(product_id: int, active: bool, admin=Depends(get_current_admin), db: Session = Depends(get_db)):
    product = db.query(Product).filter(Product.id == product_id).first()
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    product.active = active
    db.commit()
    return db.query(Product).options(joinedload(Product.images), joinedload(Product.variants)).filter(Product.id == product.id).first()


@router.patch("/variants/{variant_id}/stock")
def admin_update_stock(variant_id: int, stock_qty: int, reason: str = "manual update", admin=Depends(get_current_admin), db: Session = Depends(get_db)):
    variant = adjust_stock(db, variant_id, stock_qty, reason=reason, admin_id=admin.id)
    log_admin_action(db, admin, "inventory.adjust", "variant", variant.id, {"stock_qty": stock_qty, "reason": reason})
    db.commit()
    return {"ok": True, "variant_id": variant.id, "stock_qty": variant.stock_qty, "reserved_qty": variant.reserved_qty}


@router.get("/orders", response_model=list[OrderOut])
def admin_orders(admin=Depends(get_current_admin), db: Session = Depends(get_db)):
    return db.query(Order).options(joinedload(Order.items)).order_by(Order.created_at.desc()).all()


@router.patch("/orders/{order_id}", response_model=OrderOut)
def admin_update_order(order_id: int, payload: OrderStatusUpdate, admin=Depends(get_current_admin), db: Session = Depends(get_db)):
    allowed = {"created", "payment_created", "paid", "assembling", "ready", "shipped", "completed", "cancelled", "refund_requested", "refunded"}
    order = db.query(Order).options(joinedload(Order.items), joinedload(Order.customer)).filter(Order.id == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    if payload.status:
        if payload.status not in allowed:
            raise HTTPException(status_code=400, detail="Invalid status")
        if payload.status == "cancelled" and order.payment_status != "paid":
            for item in order.items:
                release_variant(db, item.variant_id, item.quantity)
            order.payment_status = "cancelled"
        order.status = payload.status
    if payload.delivery_status:
        order.delivery_status = payload.delivery_status
    if payload.tracking_number is not None:
        order.tracking_number = payload.tracking_number
    queue_order_status(db, order)
    log_admin_action(db, admin, "order.update", "order", order.id, payload.model_dump())
    db.commit()
    return order


@router.post("/promocodes")
def admin_create_promo(payload: PromoCodeCreate, admin=Depends(get_current_admin), db: Session = Depends(get_db)):
    promo = PromoCode(**payload.model_dump())
    promo.code = promo.code.strip().upper()
    db.add(promo)
    log_admin_action(db, admin, "promocode.create", "promocode", promo.id, {"code": promo.code})
    db.commit()
    return {"ok": True, "id": promo.id}


@router.get("/notifications")
def admin_notifications(admin=Depends(get_current_admin), db: Session = Depends(get_db)):
    return db.query(Notification).order_by(Notification.created_at.desc()).limit(100).all()


@router.post("/products/import-csv")
async def admin_import_products_csv(file: UploadFile = File(...), admin=Depends(get_current_admin), db: Session = Depends(get_db)):
    """Import products from CSV.

    Required columns:
    sku,title,slug,price,size,variant_sku,stock_qty

    Optional:
    brand,description,currency,category,gender,image_url,color,active
    """
    raw = await file.read()
    text = raw.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    required = {"sku", "title", "slug", "price", "size", "variant_sku", "stock_qty"}
    if not required.issubset(set(reader.fieldnames or [])):
        raise HTTPException(status_code=400, detail=f"CSV must contain columns: {sorted(required)}")

    created_products = 0
    updated_variants = 0

    for row in reader:
        sku = row["sku"].strip()
        product = db.query(Product).filter(Product.sku == sku).first()
        if not product:
            product = Product(
                sku=sku,
                title=row["title"].strip(),
                slug=row["slug"].strip(),
                brand=row.get("brand", "FLASHIN") or "FLASHIN",
                description=row.get("description", "") or "",
                price=float(row["price"]),
                currency=row.get("currency", "RUB") or "RUB",
                category=row.get("category", "Clothing") or "Clothing",
                gender=row.get("gender", "unisex") or "unisex",
                active=(row.get("active", "true").lower() != "false"),
            )
            db.add(product)
            db.flush()
            created_products += 1
            if row.get("image_url"):
                db.add(ProductImage(product_id=product.id, url=row["image_url"], sort_order=0))
        else:
            product.title = row["title"].strip()
            product.price = float(row["price"])
            product.active = (row.get("active", "true").lower() != "false")

        variant_sku = row["variant_sku"].strip()
        variant = db.query(ProductVariant).filter(ProductVariant.sku == variant_sku).first()
        if not variant:
            db.add(ProductVariant(
                product_id=product.id,
                size=row["size"].strip(),
                color=row.get("color", "") or "",
                sku=variant_sku,
                stock_qty=int(row["stock_qty"]),
                reserved_qty=0,
            ))
        else:
            variant.stock_qty = int(row["stock_qty"])
            variant.color = row.get("color", variant.color) or variant.color
        updated_variants += 1

    db.commit()
    return {"ok": True, "created_products": created_products, "updated_variants": updated_variants}


@router.get("/orders/export-csv")
def admin_export_orders_csv(admin=Depends(get_current_admin), db: Session = Depends(get_db)):
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["order_id", "created_at", "status", "payment_status", "delivery_status", "total_amount", "currency", "customer_id", "delivery_type", "address", "tracking_number"])
    for order in db.query(Order).order_by(Order.created_at.desc()).all():
        writer.writerow([
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
        ])
    return Response(content=output.getvalue(), media_type="text/csv", headers={"Content-Disposition": "attachment; filename=flashin_orders.csv"})



from ..models import AuditLog, MoySkladMappingRule, MoySkladConflict, Customer, CrmProfile


@router.get("/audit-logs", response_model=list[AuditLogOut])
def admin_audit_logs(admin=Depends(get_current_admin), db: Session = Depends(get_db)):
    return db.query(AuditLog).order_by(AuditLog.created_at.desc()).limit(200).all()



@router.get("/products/{product_id}", response_model=ProductOut)
def admin_product_detail(product_id: int, admin=Depends(get_current_admin), db: Session = Depends(get_db)):
    require_permission(db, admin, "products.read")
    product = db.query(Product).options(joinedload(Product.images), joinedload(Product.variants)).filter(Product.id == product_id).first()
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    return product


@router.patch("/products/{product_id}", response_model=ProductOut)
def admin_product_update(product_id: int, payload: AdminProductUpdate, admin=Depends(get_current_admin), db: Session = Depends(get_db)):
    require_permission(db, admin, "products.write")
    product = db.query(Product).filter(Product.id == product_id).first()
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    data = payload.model_dump(exclude_unset=True)
    for key, value in data.items():
        setattr(product, key, value)
    log_admin_action(db, admin, "product.update", "product", product.id, data)
    db.commit()
    return db.query(Product).options(joinedload(Product.images), joinedload(Product.variants)).filter(Product.id == product.id).first()


@router.get("/customers")
def admin_customers(admin=Depends(get_current_admin), db: Session = Depends(get_db)):
    require_permission(db, admin, "customers.read")
    rows = db.query(Customer).order_by(Customer.created_at.desc()).limit(500).all()
    return [{"id": c.id, "telegram_id": c.telegram_id, "username": c.username, "first_name": c.first_name, "phone": c.phone} for c in rows]


@router.get("/customers/{customer_id}")
def admin_customer_detail(customer_id: int, admin=Depends(get_current_admin), db: Session = Depends(get_db)):
    require_permission(db, admin, "customers.read")
    c = db.query(Customer).filter(Customer.id == customer_id).first()
    if not c:
        raise HTTPException(status_code=404, detail="Customer not found")
    profile = db.query(CrmProfile).filter(CrmProfile.customer_id == c.id).first()
    return {"customer": {"id": c.id, "telegram_id": c.telegram_id, "username": c.username, "first_name": c.first_name, "phone": c.phone}, "profile": profile}


@router.post("/moysklad/mapping-rules", response_model=MoySkladMappingRuleOut)
def create_mapping_rule(payload: MoySkladMappingRuleCreate, admin=Depends(get_current_admin), db: Session = Depends(get_db)):
    require_permission(db, admin, "products.write")
    rule = MoySkladMappingRule(**payload.model_dump())
    db.add(rule)
    log_admin_action(db, admin, "moysklad.mapping.create", "moysklad_mapping_rule", "", payload.model_dump())
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
