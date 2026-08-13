import base64
import math
import re
from urllib.parse import urlparse

import httpx
from sqlalchemy.orm import Session

from ..config import get_settings
from ..database import utcnow_naive
from ..models import MoySkladSyncLog, Product, ProductVariant
from .inventory import adjust_stock
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


def _reference_id(value: object) -> str:
    if not isinstance(value, dict):
        return ""
    direct = str(value.get("id") or "").strip()
    if direct:
        return direct
    meta = value.get("meta")
    if not isinstance(meta, dict):
        return ""
    href = str(meta.get("href") or "").strip()
    if not href:
        return ""
    path = urlparse(href).path.rstrip("/")
    return path.rsplit("/", 1)[-1] if path else ""


def _sale_price_type_keys(sale_price: object) -> set[str]:
    if not isinstance(sale_price, dict):
        return set()
    price_type = sale_price.get("priceType")
    if not isinstance(price_type, dict):
        return set()
    keys = {
        str(price_type.get("name") or "").strip().casefold(),
        str(price_type.get("id") or "").strip().casefold(),
        _reference_id(price_type).casefold(),
    }
    return {key for key in keys if key}


def _price_from_moysklad(row: dict, preferred_price_type: str = "") -> float:
    preferred = str(preferred_price_type or "").strip().casefold()
    candidates: list[tuple[set[str], float]] = []
    for sale_price in row.get("salePrices") or []:
        try:
            value = float(sale_price.get("value"))
        except (AttributeError, TypeError, ValueError):
            continue
        if math.isfinite(value) and value > 0:
            candidates.append((_sale_price_type_keys(sale_price), round(value / 100, 2)))

    if preferred:
        for keys, value in candidates:
            if preferred in keys:
                return value
        return 0.0
    return candidates[0][1] if candidates else 0.0


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


def _attribute_names(value: object) -> set[str]:
    return {
        item.strip().casefold()
        for item in str(value or "").split(",")
        if item.strip()
    }


def _attribute_value(
    row: dict,
    configured_names: object,
    *,
    direct_keys: tuple[str, ...] = (),
) -> str:
    for key in direct_keys:
        direct = row.get(key)
        if direct is not None and str(direct).strip():
            return str(direct).strip()

    expected = _attribute_names(configured_names)
    if not expected:
        return ""
    attributes = row.get("attributes") or []
    if not isinstance(attributes, list):
        return ""

    for attribute in attributes:
        if not isinstance(attribute, dict):
            continue
        name = str(attribute.get("name") or "").strip().casefold()
        if name not in expected:
            continue
        value = attribute.get("value")
        if isinstance(value, dict):
            value = value.get("name") or value.get("value") or value.get("id")
        cleaned = str(value or "").strip()
        if cleaned:
            return cleaned
    return ""


def _row_type(row: dict) -> str:
    meta = row.get("meta")
    if isinstance(meta, dict):
        value = str(meta.get("type") or "").strip().casefold()
        if value:
            return value
    return str(row.get("type") or "").strip().casefold()


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


def _apply_synced_stock(
    db: Session,
    variant: ProductVariant,
    external_stock: int,
    *,
    sync_type: str,
    admin_id: int | None,
) -> ProductVariant:
    target_stock = max(external_stock, int(variant.reserved_qty or 0))
    if target_stock == variant.stock_qty:
        return variant
    return adjust_stock(
        db,
        variant.id,
        target_stock,
        reason=f"MoySklad {sync_type} sync",
        admin_id=admin_id,
    )


async def sync_assortment_to_catalog(
    db: Session,
    sync_type: str = "manual",
    admin_id: int | None = None,
) -> MoySkladSyncLog:
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
                price = _price_from_moysklad(row, settings.moysklad_sale_price_type)
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

                missing_price_message = (
                    f"Configured sale price type '{settings.moysklad_sale_price_type}' is missing or invalid"
                    if settings.moysklad_sale_price_type.strip()
                    else "No positive sale price was supplied"
                )
                if not product:
                    if price <= 0:
                        log_conflict(
                            db,
                            moysklad_id,
                            sku,
                            "missing_price",
                            f"Product imported inactive because {missing_price_message.lower()}",
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
                            f"Existing local price preserved because {missing_price_message.lower()}",
                        )
                    product.category = apply_mapping(
                        db,
                        "category",
                        row.get("pathName", product.category),
                        product.category,
                    )

                raw_size = _attribute_value(
                    row,
                    settings.moysklad_size_attribute_names,
                    direct_keys=("size",),
                )
                if not raw_size and _row_type(row) == "variant":
                    log_conflict(
                        db,
                        moysklad_id,
                        sku,
                        "missing_size",
                        "Variant has no configured size attribute; ONE SIZE fallback applied",
                    )
                size = apply_mapping(db, "size", raw_size or "ONE SIZE", "ONE SIZE")
                raw_color = _attribute_value(
                    row,
                    settings.moysklad_color_attribute_names,
                    direct_keys=("color",),
                )
                color = apply_mapping(db, "color", raw_color, "") if raw_color else ""

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
                        color=str(color)[:64],
                        sku=sku,
                        moysklad_id=moysklad_id,
                        stock_qty=0,
                        reserved_qty=0,
                    )
                    db.add(variant)
                    db.flush()
                    if external_stock is not None:
                        variant = _apply_synced_stock(
                            db,
                            variant,
                            external_stock,
                            sync_type=sync_type,
                            admin_id=admin_id,
                        )
                    upserted_variants += 1
                else:
                    variant.moysklad_id = moysklad_id
                    variant.size = str(size)[:32]
                    variant.color = str(color)[:64]
                    if external_stock is not None:
                        if external_stock < variant.reserved_qty:
                            log_conflict(
                                db,
                                moysklad_id,
                                sku,
                                "stock_below_reserved",
                                "External stock is below the local reserved quantity; reservation preserved",
                            )
                        variant = _apply_synced_stock(
                            db,
                            variant,
                            external_stock,
                            sync_type=sync_type,
                            admin_id=admin_id,
                        )
                        upserted_variants += 1

            db.commit()
            offset += len(rows)
            if len(rows) < settings.moysklad_sync_limit:
                break

        log.status = "success"
        log.products_seen = seen
        log.products_upserted = upserted_products
        log.variants_upserted = upserted_variants
        log.finished_at = utcnow_naive()
        db.commit()
        return log
    except Exception as exc:
        db.rollback()
        log = db.query(MoySkladSyncLog).filter(MoySkladSyncLog.id == log.id).first() or log
        log.status = "failed"
        log.error = f"{exc.__class__.__name__}: {exc}"[:2000]
        log.finished_at = utcnow_naive()
        db.add(log)
        db.commit()
        return log
