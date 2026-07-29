import base64
import math
import re
from datetime import datetime

import httpx
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import MoySkladSyncLog, Product, ProductVariant
from .moysklad_mapping import apply_mapping, log_conflict


def _headers() -> dict:
    settings = get_settings()
    headers = {
        "Accept": "application/json;charset=utf-8",
        "Content-Type": "application/json",
    }
    if settings.moysklad_token:
        headers["Authorization"] = f"Bearer {settings.moysklad_token}"
    elif settings.moysklad_login and settings.moysklad_password:
        token = base64.b64encode(
            f"{settings.moysklad_login}:{settings.moysklad_password}".encode()
        ).decode()
        headers["Authorization"] = f"Basic {token}"
    return headers


async def fetch_assortment(limit: int = 100, offset: int = 0) -> dict:
    settings = get_settings()
    safe_limit = max(1, min(int(limit), 1000))
    safe_offset = max(0, int(offset))
    url = f"{settings.moysklad_base_url}/entity/assortment"
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(
            url,
            headers=_headers(),
            params={"limit": safe_limit, "offset": safe_offset},
        )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError("MoySklad assortment response must be an object")
    return payload


def _price_from_moysklad(row: dict) -> float:
    sale_prices = row.get("salePrices") or []
    for sale_price in sale_prices:
        try:
            value = float(sale_price.get("value"))
        except (AttributeError, TypeError, ValueError):
            continue
        if math.isfinite(value) and value > 0:
            return round(value / 100, 2)
    return 0.0


def _stock_from_moysklad(row: dict) -> int | None:
    """Return stock only when the provider actually supplied a valid value.

    Assortment responses do not always contain stock fields. Treating a missing
    field as zero would incorrectly sell out the local catalog.
    """
    for key in ("effectiveStock", "stock", "quantity"):
        if key not in row or row[key] is None:
            continue
        try:
            value = float(row[key])
        except (TypeError, ValueError):
            return None
        if not math.isfinite(value):
            return None
        return max(int(value), 0)
    return None


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return (slug or "product")[:230]


def _unique_slug(db: Session, sku: str, moysklad_id: str) -> str:
    base = _slugify(sku)
    if not db.query(Product.id).filter(Product.slug == base).first():
        return base
    suffix = re.sub(r"[^a-z0-9]", "", moysklad_id.lower())[:12] or "moysklad"
    candidate = f"{base[:242]}-{suffix}"[:255]
    if db.query(Product.id).filter(Product.slug == candidate).first():
        raise ValueError(f"Could not create unique slug for MoySklad item {moysklad_id}")
    return candidate


async def sync_assortment_to_catalog(db: Session, sync_type: str = "manual") -> MoySkladSyncLog:
    settings = get_settings()
    log = MoySkladSyncLog(sync_type=sync_type, status="started")
    db.add(log)
    db.commit()
    db.refresh(log)

    try:
        offset = 0
        seen = upserted_products = upserted_variants = 0
        while True:
            data = await fetch_assortment(limit=settings.moysklad_sync_limit, offset=offset)
            rows = data.get("rows", [])
            if not isinstance(rows, list):
                raise ValueError("MoySklad assortment rows must be a list")
            if not rows:
                break

            for row in rows:
                if not isinstance(row, dict):
                    continue
                seen += 1
                moysklad_id = str(row.get("id") or "").strip()
                raw_sku = row.get("article") or row.get("code")
                sku = str(raw_sku or moysklad_id).strip()
                if not moysklad_id or not sku:
                    log_conflict(
                        db,
                        moysklad_id,
                        sku,
                        "missing_identity",
                        "MoySklad item has no stable id or SKU",
                    )
                    continue
                if len(sku) > 120:
                    log_conflict(
                        db,
                        moysklad_id,
                        sku[:120],
                        "sku_too_long",
                        "MoySklad SKU exceeds 120 characters",
                    )
                    continue
                if not raw_sku:
                    log_conflict(
                        db,
                        moysklad_id,
                        sku,
                        "missing_sku",
                        "MoySklad item has no article/code; using id as SKU",
                    )

                name = str(row.get("name") or sku).strip()[:255]
                price = _price_from_moysklad(row)
                external_stock = _stock_from_moysklad(row)
                product = db.query(Product).filter(Product.sku == sku).first()

                if product and product.moysklad_id and product.moysklad_id != moysklad_id:
                    log_conflict(
                        db,
                        moysklad_id,
                        sku,
                        "sku_collision",
                        f"SKU already belongs to MoySklad item {product.moysklad_id}",
                    )
                    continue

                if not product:
                    if price <= 0:
                        log_conflict(
                            db,
                            moysklad_id,
                            sku,
                            "missing_price",
                            "Product imported inactive because no positive sale price was supplied",
                        )
                    product = Product(
                        sku=sku,
                        moysklad_id=moysklad_id,
                        title=name,
                        slug=_unique_slug(db, sku, moysklad_id),
                        brand="FLASHIN",
                        description=str(row.get("description") or ""),
                        price=price,
                        currency=settings.moysklad_default_currency,
                        category=apply_mapping(
                            db,
                            "category",
                            row.get("pathName", "Clothing"),
                            "Clothing",
                        ),
                        active=price > 0,
                    )
                    db.add(product)
                    db.flush()
                    upserted_products += 1
                else:
                    product.moysklad_id = moysklad_id
                    product.title = name
                    product.description = str(row.get("description") or product.description or "")
                    if price > 0:
                        product.price = price
                    else:
                        log_conflict(
                            db,
                            moysklad_id,
                            sku,
                            "missing_price",
                            "Existing local price preserved because provider price is missing",
                        )
                    product.category = apply_mapping(
                        db,
                        "category",
                        row.get("pathName", product.category),
                        product.category,
                    )

                size = apply_mapping(
                    db,
                    "size",
                    row.get("size") or row.get("uom", {}).get("name") or "ONE SIZE",
                    "ONE SIZE",
                )
                variant = db.query(ProductVariant).filter(ProductVariant.sku == sku).first()
                if variant and variant.product_id != product.id:
                    log_conflict(
                        db,
                        moysklad_id,
                        sku,
                        "variant_sku_collision",
                        "Variant SKU belongs to another local product",
                    )
                    continue

                if not variant:
                    if external_stock is None:
                        log_conflict(
                            db,
                            moysklad_id,
                            sku,
                            "missing_stock",
                            "Provider did not supply stock; new variant starts at zero",
                        )
                    variant = ProductVariant(
                        product_id=product.id,
                        size=str(size)[:32],
                        sku=sku,
                        moysklad_id=moysklad_id,
                        stock_qty=external_stock if external_stock is not None else 0,
                        reserved_qty=0,
                    )
                    db.add(variant)
                    upserted_variants += 1
                else:
                    variant.moysklad_id = moysklad_id
                    variant.size = str(size)[:32]
                    if external_stock is not None:
                        if external_stock < variant.reserved_qty:
                            log_conflict(
                                db,
                                moysklad_id,
                                sku,
                                "stock_below_reserved",
                                "External stock is below the local reserved quantity; reservation preserved",
                            )
                        variant.stock_qty = max(external_stock, variant.reserved_qty)
                        upserted_variants += 1

            db.commit()
            offset += len(rows)
            if len(rows) < settings.moysklad_sync_limit:
                break

        log.status = "success"
        log.products_seen = seen
        log.products_upserted = upserted_products
        log.variants_upserted = upserted_variants
        log.finished_at = datetime.utcnow()
        db.commit()
        return log
    except Exception as exc:
        db.rollback()
        log = db.query(MoySkladSyncLog).filter(MoySkladSyncLog.id == log.id).first() or log
        log.status = "failed"
        log.error = f"{exc.__class__.__name__}: {exc}"[:2000]
        log.finished_at = datetime.utcnow()
        db.add(log)
        db.commit()
        return log
