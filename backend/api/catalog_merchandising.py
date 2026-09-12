from __future__ import annotations

import json
import math
from collections import defaultdict
from datetime import datetime
from typing import Literal
from urllib.parse import quote, urlsplit

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from ..catalog_models import (
    ProductExternalAvailability,
    ProductFeedback,
    ProductMerchandising,
    ProductVideo,
)
from ..config import get_settings
from ..database import get_db, utcnow_naive
from ..models import (
    CartItem,
    Customer,
    InventoryMovement,
    OrderItem,
    Product,
    ProductImage,
    ProductRecommendation,
    ProductVariant,
)
from ..security import get_current_admin, get_current_customer
from ..services.audit import log_admin_action
from ..services.inventory import adjust_stock
from ..services.rbac import has_permission, require_permission

router = APIRouter(prefix="/catalog", tags=["catalog"])
settings = get_settings()

AvailabilityStatus = Literal["in_stock", "preorder", "made_to_order", "out_of_stock"]
CatalogSort = Literal["grid", "price_asc", "price_desc", "newest", "rating_desc"]


class CatalogVideoIn(BaseModel):
    url: str
    title: str = ""
    sort_order: int = 0
    active: bool = True


class CatalogExternalLinkIn(BaseModel):
    source_name: str
    url: str
    availability_status: AvailabilityStatus = "in_stock"
    price: float | None = Field(default=None, ge=0)
    currency: str = "RUB"
    active: bool = True
    sort_order: int = 0


class CatalogVariantIn(BaseModel):
    id: int | None = None
    size: str
    color: str = ""
    sku: str
    moysklad_id: str = ""
    stock_qty: int = Field(default=0, ge=0)


class CatalogProductCreate(BaseModel):
    sku: str
    title: str
    slug: str
    brand: str = "FLASHIN"
    description: str = ""
    price: float = Field(gt=0)
    old_price: float | None = Field(default=None, ge=0)
    currency: str = "RUB"
    category: str = "Clothing"
    gender: str = "unisex"
    active: bool = True
    is_drop: bool = False
    is_rare: bool = False
    moysklad_id: str = ""
    drop_starts_at: datetime | None = None
    vip_only_until: datetime | None = None
    availability_status: AvailabilityStatus = "in_stock"
    material: str = ""
    season: str = ""
    badges: list[str] = Field(default_factory=list)
    grid_rank: int = 1000
    sale_starts_at: datetime | None = None
    sale_ends_at: datetime | None = None
    showroom_fitting_enabled: bool = True
    images: list[str] = Field(default_factory=list)
    videos: list[CatalogVideoIn] = Field(default_factory=list)
    external_links: list[CatalogExternalLinkIn] = Field(default_factory=list)
    variants: list[CatalogVariantIn] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid")


class CatalogProductUpdate(BaseModel):
    sku: str | None = None
    title: str | None = None
    slug: str | None = None
    brand: str | None = None
    description: str | None = None
    price: float | None = None
    old_price: float | None = None
    currency: str | None = None
    category: str | None = None
    gender: str | None = None
    active: bool | None = None
    is_drop: bool | None = None
    is_rare: bool | None = None
    moysklad_id: str | None = None
    drop_starts_at: datetime | None = None
    vip_only_until: datetime | None = None
    availability_status: AvailabilityStatus | None = None
    material: str | None = None
    season: str | None = None
    badges: list[str] | None = None
    grid_rank: int | None = None
    sale_starts_at: datetime | None = None
    sale_ends_at: datetime | None = None
    showroom_fitting_enabled: bool | None = None
    images: list[str] | None = None
    videos: list[CatalogVideoIn] | None = None
    external_links: list[CatalogExternalLinkIn] | None = None
    variants: list[CatalogVariantIn] | None = None
    remove_variant_ids: list[int] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid")


class RecommendationSetIn(BaseModel):
    product_ids: list[int] = Field(default_factory=list, max_length=12)


class FeedbackIn(BaseModel):
    rating: int = Field(ge=1, le=5)
    comment: str = Field(default="", max_length=2000)


class FeedbackModerationIn(BaseModel):
    status: Literal["published", "hidden"]


def _clean_text(value: object, field: str, limit: int, *, required: bool = False) -> str:
    cleaned = str(value or "").strip()
    if required and not cleaned:
        raise HTTPException(status_code=400, detail=f"{field} is required")
    if len(cleaned) > limit:
        raise HTTPException(status_code=400, detail=f"{field} is too long")
    return cleaned


def _clean_url(value: object, field: str) -> str:
    cleaned = _clean_text(value, field, 2048, required=True)
    parsed = urlsplit(cleaned)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise HTTPException(status_code=400, detail=f"{field} must be an http(s) URL")
    return cleaned


def _positive_price(value: object, field: str = "price") -> float:
    try:
        price = float(value)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=f"{field} must be numeric") from exc
    if not math.isfinite(price) or price <= 0:
        raise HTTPException(status_code=400, detail=f"{field} must be finite and positive")
    return round(price, 2)


def _optional_price(value: object, field: str = "old_price") -> float | None:
    if value is None:
        return None
    try:
        price = float(value)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=f"{field} must be numeric") from exc
    if not math.isfinite(price) or price < 0:
        raise HTTPException(status_code=400, detail=f"{field} must be finite and non-negative")
    return round(price, 2)


def _normalize_badges(values: list[str]) -> list[str]:
    if len(values) > 16:
        raise HTTPException(status_code=400, detail="Too many product badges")
    result: list[str] = []
    for raw in values:
        badge = str(raw or "").strip().lower().replace(" ", "_")
        if not badge or len(badge) > 40:
            raise HTTPException(status_code=400, detail="Invalid product badge")
        if not all(char.isalnum() or char in {"_", "-"} for char in badge):
            raise HTTPException(status_code=400, detail="Invalid product badge")
        if badge not in result:
            result.append(badge)
    return result


def _badges(row: ProductMerchandising | None, product: Product) -> list[str]:
    values: list[str] = []
    if row:
        try:
            decoded = json.loads(row.badges_json or "[]")
            if isinstance(decoded, list):
                values = [str(item).strip().lower() for item in decoded if str(item).strip()]
        except (TypeError, ValueError):
            values = []
    if product.is_drop and "drop" not in values:
        values.append("drop")
    if product.is_rare and "limited" not in values:
        values.append("limited")
    if product.old_price and product.old_price > product.price and "sale" not in values:
        values.append("sale")
    return values


def _load_context(db: Session, product_ids: list[int]) -> dict[str, object]:
    if not product_ids:
        return {"merch": {}, "videos": {}, "external": {}, "feedback": {}, "recommendations": {}}
    merch = {
        row.product_id: row
        for row in db.query(ProductMerchandising).filter(ProductMerchandising.product_id.in_(product_ids)).all()
    }
    videos: dict[int, list[ProductVideo]] = defaultdict(list)
    for row in (
        db.query(ProductVideo)
        .filter(ProductVideo.product_id.in_(product_ids), ProductVideo.active.is_(True))
        .order_by(ProductVideo.sort_order.asc(), ProductVideo.id.asc())
        .all()
    ):
        videos[row.product_id].append(row)
    external: dict[int, list[ProductExternalAvailability]] = defaultdict(list)
    for row in (
        db.query(ProductExternalAvailability)
        .filter(
            ProductExternalAvailability.product_id.in_(product_ids),
            ProductExternalAvailability.active.is_(True),
        )
        .order_by(ProductExternalAvailability.sort_order.asc(), ProductExternalAvailability.id.asc())
        .all()
    ):
        external[row.product_id].append(row)
    feedback: dict[int, list[ProductFeedback]] = defaultdict(list)
    for row in (
        db.query(ProductFeedback)
        .filter(ProductFeedback.product_id.in_(product_ids), ProductFeedback.status == "published")
        .all()
    ):
        feedback[row.product_id].append(row)
    recommendations: dict[int, list[ProductRecommendation]] = defaultdict(list)
    for row in (
        db.query(ProductRecommendation)
        .filter(ProductRecommendation.product_id.in_(product_ids))
        .order_by(ProductRecommendation.score.desc(), ProductRecommendation.id.asc())
        .all()
    ):
        recommendations[row.product_id].append(row)
    return {
        "merch": merch,
        "videos": videos,
        "external": external,
        "feedback": feedback,
        "recommendations": recommendations,
    }


def _serialize_product(product: Product, context: dict[str, object], *, admin: bool = False) -> dict[str, object]:
    merch: ProductMerchandising | None = context["merch"].get(product.id)  # type: ignore[index]
    videos: list[ProductVideo] = context["videos"].get(product.id, [])  # type: ignore[index]
    external: list[ProductExternalAvailability] = context["external"].get(product.id, [])  # type: ignore[index]
    feedback: list[ProductFeedback] = context["feedback"].get(product.id, [])  # type: ignore[index]
    rec_rows: list[ProductRecommendation] = context["recommendations"].get(product.id, [])  # type: ignore[index]

    images = sorted(product.images, key=lambda item: (item.sort_order, item.id))
    variants = sorted(product.variants, key=lambda item: (item.color or "", item.size or "", item.id))
    local_available_qty = sum(max(int(item.stock_qty) - int(item.reserved_qty), 0) for item in variants)
    configured_status = merch.availability_status if merch else "in_stock"
    if local_available_qty > 0:
        effective_status = "in_stock"
    elif configured_status in {"preorder", "made_to_order"}:
        effective_status = configured_status
    else:
        effective_status = "out_of_stock"
    external_available = any(item.availability_status == "in_stock" for item in external)
    rating_count = len(feedback)
    rating_average = round(sum(item.rating for item in feedback) / rating_count, 2) if rating_count else 0.0
    badges = _badges(merch, product)
    product_url = f"{settings.mini_app_url.rstrip('/')}?product={product.id}"
    share_url = f"https://t.me/share/url?url={quote(product_url)}&text={quote(product.title)}"

    result: dict[str, object] = {
        "id": product.id,
        "sku": product.sku,
        "slug": product.slug,
        "title": product.title,
        "brand": product.brand,
        "description": product.description,
        "price": product.price,
        "old_price": product.old_price,
        "currency": product.currency,
        "category": product.category,
        "gender": product.gender,
        "active": product.active,
        "is_drop": product.is_drop,
        "is_rare": product.is_rare,
        "drop_starts_at": product.drop_starts_at,
        "vip_only_until": product.vip_only_until,
        "images": [
            {"id": item.id, "url": item.url, "sort_order": item.sort_order}
            for item in images
        ],
        "videos": [
            {"id": item.id, "url": item.url, "title": item.title, "sort_order": item.sort_order}
            for item in videos
        ],
        "variants": [
            {
                "id": item.id,
                "size": item.size,
                "color": item.color,
                "sku": item.sku,
                "stock_qty": item.stock_qty,
                "reserved_qty": item.reserved_qty,
                "available_qty": item.available_qty,
                **({"moysklad_id": item.moysklad_id} if admin else {}),
            }
            for item in variants
        ],
        "merchandising": {
            "availability_status": effective_status,
            "configured_availability_status": configured_status,
            "material": merch.material if merch else "",
            "season": merch.season if merch else "",
            "badges": badges,
            "grid_rank": merch.grid_rank if merch else 1000,
            "sale_starts_at": merch.sale_starts_at if merch else None,
            "sale_ends_at": merch.sale_ends_at if merch else None,
            "showroom_fitting_enabled": merch.showroom_fitting_enabled if merch else True,
            "local_available_qty": local_available_qty,
            "external_available": external_available,
            "can_add_to_cart": local_available_qty > 0,
        },
        "external_availability": [
            {
                "id": item.id,
                "source_name": item.source_name,
                "url": item.url,
                "availability_status": item.availability_status,
                "price": item.price,
                "currency": item.currency,
                "sort_order": item.sort_order,
            }
            for item in external
        ],
        "rating": {"average": rating_average, "count": rating_count},
        "recommendation_ids": [row.recommended_product_id for row in rec_rows[:12]],
        "share": {"mini_app_url": product_url, "telegram_share_url": share_url},
    }
    if admin:
        result["moysklad_id"] = product.moysklad_id
    return result


def _product_query(db: Session, *, active_only: bool):
    query = db.query(Product).options(joinedload(Product.images), joinedload(Product.variants))
    if active_only:
        query = query.filter(Product.active.is_(True))
    return query


def _passes_filter(
    item: dict[str, object],
    *,
    q: str | None,
    brand: str | None,
    category: str | None,
    material: str | None,
    season: str | None,
    availability_status: str | None,
    badge: str | None,
    size: str | None,
    color: str | None,
    min_price: float | None,
    max_price: float | None,
) -> bool:
    def contains(value: object, needle: str | None) -> bool:
        return not needle or needle.casefold() in str(value or "").casefold()

    merch = item["merchandising"]
    variants = item["variants"]
    if q and not any(contains(item.get(field), q) for field in ("title", "brand", "description", "sku")):
        return False
    if brand and str(item.get("brand") or "").casefold() != brand.casefold():
        return False
    if category and str(item.get("category") or "").casefold() != category.casefold():
        return False
    if material and not contains(merch.get("material"), material):
        return False
    if season and str(merch.get("season") or "").casefold() != season.casefold():
        return False
    if availability_status and merch.get("availability_status") != availability_status:
        return False
    if badge and badge.casefold().replace(" ", "_") not in {str(value).casefold() for value in merch.get("badges", [])}:
        return False
    if size and not any(str(row.get("size") or "").casefold() == size.casefold() and int(row.get("available_qty") or 0) > 0 for row in variants):
        return False
    if color and not any(str(row.get("color") or "").casefold() == color.casefold() and int(row.get("available_qty") or 0) > 0 for row in variants):
        return False
    price = float(item.get("price") or 0)
    if min_price is not None and price < min_price:
        return False
    if max_price is not None and price > max_price:
        return False
    return True


@router.get("/products")
def list_catalog_products(
    q: str | None = None,
    brand: str | None = None,
    category: str | None = None,
    material: str | None = None,
    season: str | None = None,
    availability_status: AvailabilityStatus | None = None,
    badge: str | None = None,
    size: str | None = None,
    color: str | None = None,
    min_price: float | None = Query(default=None, ge=0),
    max_price: float | None = Query(default=None, ge=0),
    sort: CatalogSort = "grid",
    db: Session = Depends(get_db),
):
    if min_price is not None and max_price is not None and min_price > max_price:
        raise HTTPException(status_code=400, detail="min_price cannot exceed max_price")
    products = _product_query(db, active_only=True).limit(500).all()
    context = _load_context(db, [product.id for product in products])
    rows = [_serialize_product(product, context) for product in products]
    rows = [
        item
        for item in rows
        if _passes_filter(
            item,
            q=q,
            brand=brand,
            category=category,
            material=material,
            season=season,
            availability_status=availability_status,
            badge=badge,
            size=size,
            color=color,
            min_price=min_price,
            max_price=max_price,
        )
    ]
    if sort == "price_asc":
        rows.sort(key=lambda item: (float(item["price"]), int(item["id"])))
    elif sort == "price_desc":
        rows.sort(key=lambda item: (-float(item["price"]), int(item["id"])))
    elif sort == "newest":
        products_by_id = {product.id: product for product in products}
        rows.sort(key=lambda item: (products_by_id[int(item["id"])].created_at, int(item["id"])), reverse=True)
    elif sort == "rating_desc":
        rows.sort(key=lambda item: (-float(item["rating"]["average"]), -int(item["rating"]["count"]), int(item["id"])))
    else:
        rows.sort(key=lambda item: (int(item["merchandising"]["grid_rank"]), int(item["id"])))
    return rows


@router.get("/products/{product_id}")
def catalog_product_detail(product_id: int, db: Session = Depends(get_db)):
    product = _product_query(db, active_only=True).filter(Product.id == product_id).first()
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    context = _load_context(db, [product.id])
    item = _serialize_product(product, context)
    rec_ids = list(item["recommendation_ids"])
    if rec_ids:
        related = _product_query(db, active_only=True).filter(Product.id.in_(rec_ids)).all()
        related_context = _load_context(db, [row.id for row in related])
        by_id = {row.id: _serialize_product(row, related_context) for row in related}
        item["recommendations"] = [by_id[value] for value in rec_ids if value in by_id]
    else:
        item["recommendations"] = []
    return item


@router.get("/products/{product_id}/feedback")
def product_feedback(product_id: int, db: Session = Depends(get_db)):
    exists = db.query(Product.id).filter(Product.id == product_id, Product.active.is_(True)).first()
    if not exists:
        raise HTTPException(status_code=404, detail="Product not found")
    rows = (
        db.query(ProductFeedback)
        .filter(ProductFeedback.product_id == product_id, ProductFeedback.status == "published")
        .order_by(ProductFeedback.updated_at.desc(), ProductFeedback.id.desc())
        .limit(50)
        .all()
    )
    return [
        {"id": row.id, "rating": row.rating, "comment": row.comment, "created_at": row.created_at}
        for row in rows
    ]


@router.post("/products/{product_id}/feedback")
def upsert_product_feedback(
    product_id: int,
    payload: FeedbackIn,
    customer: Customer = Depends(get_current_customer),
    db: Session = Depends(get_db),
):
    exists = db.query(Product.id).filter(Product.id == product_id, Product.active.is_(True)).first()
    if not exists:
        raise HTTPException(status_code=404, detail="Product not found")
    comment = payload.comment.strip()
    row = (
        db.query(ProductFeedback)
        .filter(ProductFeedback.product_id == product_id, ProductFeedback.customer_id == customer.id)
        .with_for_update()
        .first()
    )
    now = utcnow_naive()
    if row:
        row.rating = payload.rating
        row.comment = comment
        row.status = "published"
        row.updated_at = now
    else:
        row = ProductFeedback(
            product_id=product_id,
            customer_id=customer.id,
            rating=payload.rating,
            comment=comment,
            status="published",
            created_at=now,
            updated_at=now,
        )
        db.add(row)
    db.commit()
    db.refresh(row)
    return {"id": row.id, "rating": row.rating, "comment": row.comment, "status": row.status}


def _replace_images(db: Session, product: Product, values: list[str]) -> None:
    if len(values) > 20:
        raise HTTPException(status_code=400, detail="Too many product images")
    urls = [_clean_url(value, "Image URL") for value in values]
    if len(set(urls)) != len(urls):
        raise HTTPException(status_code=409, detail="Duplicate product image")
    db.query(ProductImage).filter(ProductImage.product_id == product.id).delete(synchronize_session=False)
    for index, url in enumerate(urls):
        db.add(ProductImage(product_id=product.id, url=url, sort_order=index))


def _replace_videos(db: Session, product: Product, values: list[CatalogVideoIn]) -> None:
    if len(values) > 12:
        raise HTTPException(status_code=400, detail="Too many product videos")
    db.query(ProductVideo).filter(ProductVideo.product_id == product.id).delete(synchronize_session=False)
    seen: set[str] = set()
    for index, item in enumerate(values):
        url = _clean_url(item.url, "Video URL")
        if url in seen:
            raise HTTPException(status_code=409, detail="Duplicate product video")
        seen.add(url)
        db.add(
            ProductVideo(
                product_id=product.id,
                url=url,
                title=_clean_text(item.title, "Video title", 255),
                sort_order=item.sort_order if item.sort_order is not None else index,
                active=item.active,
            )
        )


def _replace_external_links(db: Session, product: Product, values: list[CatalogExternalLinkIn]) -> None:
    if len(values) > 20:
        raise HTTPException(status_code=400, detail="Too many external product links")
    db.query(ProductExternalAvailability).filter(ProductExternalAvailability.product_id == product.id).delete(synchronize_session=False)
    seen: set[str] = set()
    for index, item in enumerate(values):
        url = _clean_url(item.url, "External product URL")
        if url in seen:
            raise HTTPException(status_code=409, detail="Duplicate external product URL")
        seen.add(url)
        db.add(
            ProductExternalAvailability(
                product_id=product.id,
                source_name=_clean_text(item.source_name, "External source", 255, required=True),
                url=url,
                availability_status=item.availability_status,
                price=_optional_price(item.price, "External price") if item.price is not None else None,
                currency=_clean_text(item.currency, "External currency", 8, required=True).upper(),
                active=item.active,
                sort_order=item.sort_order if item.sort_order is not None else index,
            )
        )


def _apply_merchandising(db: Session, product: Product, data: dict[str, object]) -> None:
    merchandising_keys = {
        "availability_status",
        "material",
        "season",
        "badges",
        "grid_rank",
        "sale_starts_at",
        "sale_ends_at",
        "showroom_fitting_enabled",
    }
    if not merchandising_keys.intersection(data):
        return
    row = (
        db.query(ProductMerchandising)
        .filter(ProductMerchandising.product_id == product.id)
        .with_for_update()
        .first()
    )
    if not row:
        row = ProductMerchandising(product_id=product.id)
        db.add(row)
    if "availability_status" in data:
        row.availability_status = str(data["availability_status"])
    if "material" in data:
        row.material = _clean_text(data["material"], "Material", 255)
    if "season" in data:
        row.season = _clean_text(data["season"], "Season", 120)
    if "badges" in data:
        row.badges_json = json.dumps(_normalize_badges(list(data["badges"] or [])), ensure_ascii=False)
    if "grid_rank" in data:
        row.grid_rank = int(data["grid_rank"])
    if "sale_starts_at" in data:
        row.sale_starts_at = data["sale_starts_at"]
    if "sale_ends_at" in data:
        row.sale_ends_at = data["sale_ends_at"]
    if row.sale_starts_at and row.sale_ends_at and row.sale_starts_at >= row.sale_ends_at:
        raise HTTPException(status_code=400, detail="sale_starts_at must be before sale_ends_at")
    if "showroom_fitting_enabled" in data:
        row.showroom_fitting_enabled = bool(data["showroom_fitting_enabled"])
    row.updated_at = utcnow_naive()


def _apply_core(product: Product, data: dict[str, object]) -> None:
    text_fields = {
        "title": (255, True),
        "slug": (255, True),
        "sku": (120, True),
        "brand": (120, True),
        "description": (20000, False),
        "currency": (8, True),
        "category": (120, True),
        "gender": (32, True),
        "moysklad_id": (255, False),
    }
    for field, (limit, required) in text_fields.items():
        if field not in data:
            continue
        value = _clean_text(data[field], field, limit, required=required)
        if field in {"sku", "currency"}:
            value = value.upper()
        if field == "slug":
            value = value.lower()
        setattr(product, field, value)
    if "price" in data:
        product.price = _positive_price(data["price"])
    if "old_price" in data:
        product.old_price = _optional_price(data["old_price"])
    for field in ("active", "is_drop", "is_rare", "drop_starts_at", "vip_only_until"):
        if field in data:
            setattr(product, field, data[field])


def _apply_variants(
    db: Session,
    product: Product,
    values: list[CatalogVariantIn],
    remove_ids: list[int],
    *,
    can_inventory_write: bool,
    admin_id: int,
) -> None:
    existing = {
        row.id: row
        for row in db.query(ProductVariant).filter(ProductVariant.product_id == product.id).with_for_update().all()
    }
    seen_skus: set[str] = set()
    for item in values:
        sku = _clean_text(item.sku, "Variant SKU", 120, required=True).upper()
        if sku in seen_skus:
            raise HTTPException(status_code=409, detail=f"Duplicate variant SKU in payload: {sku}")
        seen_skus.add(sku)
        if item.id is not None:
            variant = existing.get(item.id)
            if not variant:
                raise HTTPException(status_code=409, detail=f"Variant {item.id} does not belong to product")
            variant.size = _clean_text(item.size, "Variant size", 32, required=True)
            variant.color = _clean_text(item.color, "Variant color", 64)
            variant.sku = sku
            variant.moysklad_id = _clean_text(item.moysklad_id, "Variant MoySklad ID", 255)
            if item.stock_qty != variant.stock_qty:
                if not can_inventory_write:
                    raise HTTPException(status_code=403, detail="Missing permission: inventory.write")
                adjust_stock(
                    db,
                    variant.id,
                    item.stock_qty,
                    reason="Catalog product edit",
                    admin_id=admin_id,
                )
        else:
            if item.stock_qty > 0 and not can_inventory_write:
                raise HTTPException(status_code=403, detail="Missing permission: inventory.write")
            variant = ProductVariant(
                product_id=product.id,
                size=_clean_text(item.size, "Variant size", 32, required=True),
                color=_clean_text(item.color, "Variant color", 64),
                sku=sku,
                moysklad_id=_clean_text(item.moysklad_id, "Variant MoySklad ID", 255),
                stock_qty=0,
                reserved_qty=0,
            )
            db.add(variant)
            db.flush()
            if item.stock_qty > 0:
                adjust_stock(db, variant.id, item.stock_qty, reason="Catalog variant creation", admin_id=admin_id)

    for variant_id in list(dict.fromkeys(remove_ids)):
        variant = existing.get(variant_id)
        if not variant:
            raise HTTPException(status_code=409, detail=f"Variant {variant_id} does not belong to product")
        if variant.reserved_qty > 0:
            raise HTTPException(status_code=409, detail=f"Variant {variant_id} has reserved stock")
        referenced = (
            db.query(CartItem.id).filter(CartItem.variant_id == variant_id).first()
            or db.query(OrderItem.id).filter(OrderItem.variant_id == variant_id).first()
            or db.query(InventoryMovement.id).filter(InventoryMovement.variant_id == variant_id).first()
        )
        if referenced:
            raise HTTPException(status_code=409, detail=f"Variant {variant_id} has transaction history and cannot be deleted")
        db.delete(variant)


@router.get("/admin/products")
def admin_catalog_products(admin=Depends(get_current_admin), db: Session = Depends(get_db)):
    require_permission(db, admin, "products.read")
    products = _product_query(db, active_only=False).limit(1000).all()
    context = _load_context(db, [product.id for product in products])
    rows = [_serialize_product(product, context, admin=True) for product in products]
    rows.sort(key=lambda item: (int(item["merchandising"]["grid_rank"]), int(item["id"])))
    return rows


@router.post("/admin/products")
def admin_create_catalog_product(
    payload: CatalogProductCreate,
    admin=Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    require_permission(db, admin, "products.write")
    can_inventory_write = has_permission(db, admin, "inventory.write")
    if any(item.stock_qty > 0 for item in payload.variants) and not can_inventory_write:
        raise HTTPException(status_code=403, detail="Missing permission: inventory.write")
    data = payload.model_dump()
    product = Product(
        sku=_clean_text(payload.sku, "SKU", 120, required=True).upper(),
        title=_clean_text(payload.title, "Title", 255, required=True),
        slug=_clean_text(payload.slug, "Slug", 255, required=True).lower(),
        brand=_clean_text(payload.brand, "Brand", 120, required=True),
        description=_clean_text(payload.description, "Description", 20000),
        price=_positive_price(payload.price),
        old_price=_optional_price(payload.old_price),
        currency=_clean_text(payload.currency, "Currency", 8, required=True).upper(),
        category=_clean_text(payload.category, "Category", 120, required=True),
        gender=_clean_text(payload.gender, "Gender", 32, required=True),
        active=payload.active,
        is_drop=payload.is_drop,
        is_rare=payload.is_rare,
        moysklad_id=_clean_text(payload.moysklad_id, "MoySklad ID", 255),
        drop_starts_at=payload.drop_starts_at,
        vip_only_until=payload.vip_only_until,
    )
    try:
        db.add(product)
        db.flush()
        _apply_merchandising(db, product, data)
        _replace_images(db, product, payload.images)
        _replace_videos(db, product, payload.videos)
        _replace_external_links(db, product, payload.external_links)
        _apply_variants(
            db,
            product,
            payload.variants,
            [],
            can_inventory_write=can_inventory_write,
            admin_id=admin.id,
        )
        log_admin_action(db, admin, "catalog.product.create", "product", product.id, {"sku": product.sku})
        db.commit()
    except HTTPException:
        db.rollback()
        raise
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Catalog product conflicts with existing data") from exc
    except Exception:
        db.rollback()
        raise
    product = _product_query(db, active_only=False).filter(Product.id == product.id).first()
    context = _load_context(db, [product.id])
    return _serialize_product(product, context, admin=True)


@router.put("/admin/products/{product_id}")
def admin_update_catalog_product(
    product_id: int,
    payload: CatalogProductUpdate,
    admin=Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    require_permission(db, admin, "products.write")
    data = payload.model_dump(exclude_unset=True)
    product = db.query(Product).filter(Product.id == product_id).with_for_update().first()
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    can_inventory_write = has_permission(db, admin, "inventory.write")
    try:
        _apply_core(product, data)
        _apply_merchandising(db, product, data)
        if payload.images is not None:
            _replace_images(db, product, payload.images)
        if payload.videos is not None:
            _replace_videos(db, product, payload.videos)
        if payload.external_links is not None:
            _replace_external_links(db, product, payload.external_links)
        if payload.variants is not None or payload.remove_variant_ids:
            _apply_variants(
                db,
                product,
                payload.variants or [],
                payload.remove_variant_ids,
                can_inventory_write=can_inventory_write,
                admin_id=admin.id,
            )
        log_admin_action(
            db,
            admin,
            "catalog.product.update",
            "product",
            product.id,
            {"fields": sorted(data.keys())},
        )
        db.commit()
    except HTTPException:
        db.rollback()
        raise
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Catalog product update conflicts with existing data") from exc
    except Exception:
        db.rollback()
        raise
    product = _product_query(db, active_only=False).filter(Product.id == product.id).first()
    context = _load_context(db, [product.id])
    return _serialize_product(product, context, admin=True)


@router.put("/admin/products/{product_id}/recommendations")
def admin_set_product_recommendations(
    product_id: int,
    payload: RecommendationSetIn,
    admin=Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    require_permission(db, admin, "products.write")
    product_ids = list(dict.fromkeys(payload.product_ids))
    if len(product_ids) != len(payload.product_ids):
        raise HTTPException(status_code=409, detail="Duplicate recommendation product")
    if product_id in product_ids:
        raise HTTPException(status_code=409, detail="Product cannot recommend itself")
    source = db.query(Product.id).filter(Product.id == product_id).first()
    if not source:
        raise HTTPException(status_code=404, detail="Product not found")
    existing_ids = {
        row.id for row in db.query(Product.id).filter(Product.id.in_(product_ids), Product.active.is_(True)).all()
    }
    missing = [value for value in product_ids if value not in existing_ids]
    if missing:
        raise HTTPException(status_code=409, detail={"message": "Recommendation contains unavailable product", "product_ids": missing})
    try:
        db.query(ProductRecommendation).filter(
            ProductRecommendation.product_id == product_id,
            ProductRecommendation.source == "manual_look",
        ).delete(synchronize_session=False)
        for index, recommended_id in enumerate(product_ids):
            db.add(
                ProductRecommendation(
                    product_id=product_id,
                    recommended_product_id=recommended_id,
                    score=1000 - index,
                    source="manual_look",
                )
            )
        log_admin_action(
            db,
            admin,
            "catalog.recommendations.update",
            "product",
            product_id,
            {"product_ids": product_ids},
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    return {"ok": True, "product_id": product_id, "recommendation_ids": product_ids}


@router.patch("/admin/feedback/{feedback_id}")
def admin_moderate_feedback(
    feedback_id: int,
    payload: FeedbackModerationIn,
    admin=Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    require_permission(db, admin, "products.write")
    row = db.query(ProductFeedback).filter(ProductFeedback.id == feedback_id).with_for_update().first()
    if not row:
        raise HTTPException(status_code=404, detail="Feedback not found")
    row.status = payload.status
    row.updated_at = utcnow_naive()
    log_admin_action(db, admin, "catalog.feedback.moderate", "product_feedback", row.id, {"status": row.status})
    db.commit()
    return {"ok": True, "id": row.id, "status": row.status}
