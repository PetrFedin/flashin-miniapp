from collections import defaultdict
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from ..config import get_settings
from ..database import get_db
from ..models import Product, ProductImage, ProductVariant
from ..schemas import ProductCreate, ProductOut

router = APIRouter(prefix="/products", tags=["products"])
settings = get_settings()


def _clean_text(value: str | None) -> str:
    return (value or "").strip()


def _load_active_product(db: Session, *, product_id: int | None = None, slug: str | None = None) -> Product:
    if product_id is None and not slug:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Product identifier is required")

    query = db.query(Product).options(joinedload(Product.images), joinedload(Product.variants))
    query = query.filter(Product.active.is_(True))
    if product_id is not None:
        query = query.filter(Product.id == product_id)
    if slug:
        query = query.filter(Product.slug == slug)
    product = query.first()
    if not product:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product not found")
    return product


def _discount_percent(product: Product) -> int:
    if not product.old_price or product.old_price <= product.price or product.old_price <= 0:
        return 0
    return round((product.old_price - product.price) / product.old_price * 100)


def _commerce_card(product: Product) -> dict:
    images = sorted(product.images or [], key=lambda image: image.sort_order)
    variants = sorted(product.variants or [], key=lambda variant: (_clean_text(variant.color), _clean_text(variant.size)))

    colors: dict[str, dict] = defaultdict(lambda: {"sizes": [], "available_qty": 0})
    total_available = 0
    for variant in variants:
        available = variant.available_qty
        total_available += available
        color_name = _clean_text(variant.color) or "Основной"
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
        for color, data in sorted(colors.items())
    ]

    product_url = f"{settings.mini_app_url.rstrip('/')}/product/{quote(product.slug, safe='')}"
    startapp = quote(f"product_{product.id}")
    telegram_deep_link = f"https://t.me/share/url?url={quote(product_url)}&text={quote(product.title)}"

    low_stock = 0 < total_available <= 3
    sold_out = bool(variants) and total_available <= 0
    discount_percent = _discount_percent(product)
    completeness_checks = {
        "title": bool(_clean_text(product.title)),
        "description": len(_clean_text(product.description)) >= 80,
        "price": product.price > 0,
        "images": len(images) >= 3,
        "variants": bool(variants),
        "sizes": bool({_clean_text(variant.size) for variant in variants if _clean_text(variant.size)}),
        "colors": bool({_clean_text(variant.color) for variant in variants if _clean_text(variant.color)}),
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
            "can_add_to_cart": bool(variants) and not sold_out,
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
            "ready_for_publication": completeness_score >= 85 and bool(variants) and not sold_out,
        },
    }


def _validate_product_payload(db: Session, payload: ProductCreate) -> None:
    sku = _clean_text(payload.sku)
    slug = _clean_text(payload.slug)
    if not sku or not slug:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="SKU and slug are required")

    duplicate_product = db.query(Product.id).filter((Product.sku == sku) | (Product.slug == slug)).first()
    if duplicate_product:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Product SKU or slug already exists")

    seen_skus: set[str] = set()
    seen_options: set[tuple[str, str]] = set()
    for variant in payload.variants:
        variant_sku = _clean_text(variant.get("sku"))
        size = _clean_text(variant.get("size"))
        color = _clean_text(variant.get("color"))
        if not variant_sku or not size:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Each variant requires a non-empty SKU and size",
            )
        option_key = (size.casefold(), color.casefold())
        if variant_sku.casefold() in seen_skus:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"Duplicate variant SKU: {variant_sku}")
        if option_key in seen_options:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Duplicate size/color option: {size}/{color or 'Основной'}",
            )
        seen_skus.add(variant_sku.casefold())
        seen_options.add(option_key)

    if seen_skus:
        existing_variant = db.query(ProductVariant.sku).filter(ProductVariant.sku.in_(seen_skus)).first()
        if existing_variant:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Variant SKU already exists")


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
        query = query.filter(Product.brand.ilike(f"%{brand.strip()}%"))
    if category:
        query = query.filter(Product.category == category.strip())
    if q:
        query = query.filter(Product.title.ilike(f"%{q.strip()}%"))
    products = query.order_by(Product.created_at.desc()).all()
    if size:
        normalized_size = size.strip().casefold()
        products = [
            product
            for product in products
            if any(_clean_text(variant.size).casefold() == normalized_size and variant.available_qty > 0 for variant in product.variants)
        ]
    return products


@router.get("/slug/{slug}/commerce-card")
def get_product_commerce_card_by_slug(slug: str, db: Session = Depends(get_db)):
    return _commerce_card(_load_active_product(db, slug=slug))


@router.get("/{product_id}/commerce-card")
def get_product_commerce_card(product_id: int, db: Session = Depends(get_db)):
    """Telegram Mini App product-card payload with variants, stock and sharing metadata."""
    return _commerce_card(_load_active_product(db, product_id=product_id))


@router.get("/slug/{slug}", response_model=ProductOut)
def get_product_by_slug(slug: str, db: Session = Depends(get_db)):
    return _load_active_product(db, slug=slug)


@router.get("/{product_id}", response_model=ProductOut)
def get_product(product_id: int, db: Session = Depends(get_db)):
    return _load_active_product(db, product_id=product_id)


@router.post("", response_model=ProductOut, status_code=status.HTTP_201_CREATED)
def create_product(payload: ProductCreate, db: Session = Depends(get_db)):
    # Keep public create only for local bootstrap; hide behind admin in production.
    _validate_product_payload(db, payload)
    product = Product(
        sku=_clean_text(payload.sku),
        title=_clean_text(payload.title),
        slug=_clean_text(payload.slug),
        brand=_clean_text(payload.brand) or "FLASHIN",
        description=_clean_text(payload.description),
        price=payload.price,
        old_price=payload.old_price,
        currency=_clean_text(payload.currency) or "RUB",
        category=_clean_text(payload.category) or "Clothing",
        gender=_clean_text(payload.gender) or "unisex",
        active=payload.active,
        is_drop=payload.is_drop,
        is_rare=payload.is_rare,
        drop_starts_at=payload.drop_starts_at,
        vip_only_until=payload.vip_only_until,
    )
    try:
        db.add(product)
        db.flush()
        for idx, url in enumerate(payload.images):
            cleaned_url = _clean_text(url)
            if cleaned_url:
                db.add(ProductImage(product_id=product.id, url=cleaned_url, sort_order=idx))
        for variant in payload.variants:
            db.add(
                ProductVariant(
                    product_id=product.id,
                    size=_clean_text(variant.get("size")),
                    color=_clean_text(variant.get("color")),
                    sku=_clean_text(variant.get("sku")),
                    stock_qty=max(int(variant.get("stock_qty", 0)), 0),
                    reserved_qty=0,
                )
            )
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Product data conflicts with an existing record") from exc
    except Exception:
        db.rollback()
        raise

    return get_product(product.id, db)
