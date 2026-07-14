from collections import defaultdict
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session, joinedload

from ..config import get_settings
from ..database import get_db
from ..models import Product, ProductImage, ProductVariant
from ..schemas import ProductCreate, ProductOut

router = APIRouter(prefix="/products", tags=["products"])
settings = get_settings()


def _load_active_product(db: Session, *, product_id: int | None = None, slug: str | None = None) -> Product:
    query = db.query(Product).options(joinedload(Product.images), joinedload(Product.variants))
    query = query.filter(Product.active.is_(True))
    if product_id is not None:
        query = query.filter(Product.id == product_id)
    if slug is not None:
        query = query.filter(Product.slug == slug)
    product = query.first()
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    return product


def _discount_percent(product: Product) -> int:
    if not product.old_price or product.old_price <= product.price or product.old_price <= 0:
        return 0
    return round((product.old_price - product.price) / product.old_price * 100)


def _commerce_card(product: Product) -> dict:
    images = sorted(product.images, key=lambda image: image.sort_order)
    variants = sorted(product.variants, key=lambda variant: (variant.color or "", variant.size or ""))

    colors: dict[str, dict] = defaultdict(lambda: {"sizes": [], "available_qty": 0})
    total_available = 0
    for variant in variants:
        available = variant.available_qty
        total_available += available
        color_name = variant.color.strip() or "Основной"
        colors[color_name]["available_qty"] += available
        colors[color_name]["sizes"].append(
            {
                "variant_id": variant.id,
                "sku": variant.sku,
                "size": variant.size,
                "stock_qty": variant.stock_qty,
                "reserved_qty": variant.reserved_qty,
                "available_qty": available,
                "available": available > 0,
            }
        )

    color_options = [
        {
            "name": color,
            "available": data["available_qty"] > 0,
            "available_qty": data["available_qty"],
            "sizes": data["sizes"],
        }
        for color, data in colors.items()
    ]

    product_url = f"{settings.mini_app_url.rstrip('/')}/product/{product.slug}"
    startapp = quote(f"product_{product.id}")
    telegram_deep_link = f"https://t.me/share/url?url={quote(product_url)}&text={quote(product.title)}"

    low_stock = 0 < total_available <= 3
    sold_out = total_available <= 0
    discount_percent = _discount_percent(product)
    completeness_checks = {
        "title": bool(product.title.strip()),
        "description": len(product.description.strip()) >= 80,
        "price": product.price > 0,
        "images": len(images) >= 3,
        "variants": bool(variants),
        "sizes": len({variant.size for variant in variants if variant.size}) >= 1,
        "colors": len({variant.color for variant in variants if variant.color}) >= 1,
    }
    completeness_score = round(sum(completeness_checks.values()) / len(completeness_checks) * 100)

    badges: list[str] = []
    if product.is_drop:
        badges.append("DROP")
    if product.is_rare:
        badges.append("LIMITED")
    if discount_percent:
        badges.append(f"-{discount_percent}%")
    if low_stock:
        badges.append("МАЛО")
    if sold_out:
        badges.append("НЕТ В НАЛИЧИИ")

    return {
        "product": {
            "id": product.id,
            "sku": product.sku,
            "slug": product.slug,
            "title": product.title,
            "brand": product.brand,
            "description": product.description,
            "category": product.category,
            "gender": product.gender,
            "price": product.price,
            "old_price": product.old_price,
            "currency": product.currency,
            "discount_percent": discount_percent,
            "is_drop": product.is_drop,
            "is_rare": product.is_rare,
            "drop_starts_at": product.drop_starts_at,
            "vip_only_until": product.vip_only_until,
            "badges": badges,
        },
        "gallery": [
            {
                "id": image.id,
                "url": image.url,
                "sort_order": image.sort_order,
                "is_cover": index == 0,
            }
            for index, image in enumerate(images)
        ],
        "options": {
            "colors": color_options,
            "total_available_qty": total_available,
            "sold_out": sold_out,
            "low_stock": low_stock,
            "selection_required": bool(variants),
        },
        "purchase": {
            "can_add_to_cart": not sold_out,
            "requires_variant": bool(variants),
            "supports_promo_code": True,
            "supports_loyalty_points": True,
            "supports_gift_order": True,
            "supports_telegram_stars": True,
        },
        "telegram": {
            "mini_app_url": product_url,
            "share_url": telegram_deep_link,
            "startapp_parameter": startapp,
            "haptic_events": ["variant_selected", "added_to_cart", "added_to_wishlist"],
        },
        "content_quality": {
            "score": completeness_score,
            "checks": completeness_checks,
            "ready_for_publication": completeness_score >= 85 and not sold_out,
        },
    }


@router.get("", response_model=list[ProductOut])
def list_products(
    brand: str | None = None,
    size: str | None = None,
    category: str | None = None,
    q: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    query = db.query(Product).options(joinedload(Product.images), joinedload(Product.variants)).filter(Product.active.is_(True))
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


@router.get("/{product_id}/commerce-card")
def get_product_commerce_card(product_id: int, db: Session = Depends(get_db)):
    """Telegram Mini App product-card payload with variants, stock and sharing metadata."""
    return _commerce_card(_load_active_product(db, product_id=product_id))


@router.get("/slug/{slug}/commerce-card")
def get_product_commerce_card_by_slug(slug: str, db: Session = Depends(get_db)):
    return _commerce_card(_load_active_product(db, slug=slug))


@router.get("/{product_id}", response_model=ProductOut)
def get_product(product_id: int, db: Session = Depends(get_db)):
    return _load_active_product(db, product_id=product_id)


@router.get("/slug/{slug}", response_model=ProductOut)
def get_product_by_slug(slug: str, db: Session = Depends(get_db)):
    return _load_active_product(db, slug=slug)


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
        db.add(
            ProductVariant(
                product_id=product.id,
                size=variant["size"],
                color=variant.get("color", ""),
                sku=variant["sku"],
                stock_qty=int(variant.get("stock_qty", 0)),
                reserved_qty=0,
            )
        )
    db.commit()
    return get_product(product.id, db)
