from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session, joinedload
from ..database import get_db
from ..models import Product, ProductImage, ProductVariant
from ..schemas import ProductCreate, ProductOut

router = APIRouter(prefix="/products", tags=["products"])


@router.get("", response_model=list[ProductOut])
def list_products(
    brand: str | None = None,
    size: str | None = None,
    category: str | None = None,
    q: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    query = db.query(Product).options(joinedload(Product.images), joinedload(Product.variants)).filter(Product.active == True)
    if brand:
        query = query.filter(Product.brand.ilike(f"%{brand}%"))
    if category:
        query = query.filter(Product.category == category)
    if q:
        query = query.filter(Product.title.ilike(f"%{q}%"))
    products = query.order_by(Product.created_at.desc()).all()
    if size:
        products = [p for p in products if any(v.size == size and v.available_qty > 0 for v in p.variants)]
    return products


@router.get("/{product_id}", response_model=ProductOut)
def get_product(product_id: int, db: Session = Depends(get_db)):
    product = (
        db.query(Product)
        .options(joinedload(Product.images), joinedload(Product.variants))
        .filter(Product.id == product_id, Product.active == True)
        .first()
    )
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    return product


@router.get("/slug/{slug}", response_model=ProductOut)
def get_product_by_slug(slug: str, db: Session = Depends(get_db)):
    product = (
        db.query(Product)
        .options(joinedload(Product.images), joinedload(Product.variants))
        .filter(Product.slug == slug, Product.active == True)
        .first()
    )
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    return product


@router.post("", response_model=ProductOut)
def create_product(payload: ProductCreate, db: Session = Depends(get_db)):
    # Keep public create only for local bootstrap; hide behind admin in production.
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
    for variant in payload.variants:
        db.add(ProductVariant(
            product_id=product.id,
            size=variant["size"],
            color=variant.get("color", ""),
            sku=variant["sku"],
            stock_qty=int(variant.get("stock_qty", 0)),
            reserved_qty=0,
        ))
    db.commit()
    return get_product(product.id, db)
