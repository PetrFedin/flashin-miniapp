import base64
import math
import re
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

import httpx
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import MoySkladSyncLog, Product, ProductVariant
from .moysklad_mapping import apply_mapping, log_conflict

MAX_MOYSKLAD_PAGES = 1000
MAX_MOYSKLAD_ROWS = 100_000


class MoySkladSyncInProgress(RuntimeError):
    pass


def _headers() -> dict:
    settings = get_settings()
    headers = {
        "Accept": "application/json;charset=utf-8",
        "Content-Type": "application/json",
    }
    if settings.moysklad_token.strip():
        headers["Authorization"] = f"Bearer {settings.moysklad_token.strip()}"
    elif settings.moysklad_login.strip() and settings.moysklad_password:
        token = base64.b64encode(
            f"{settings.moysklad_login.strip()}:{settings.moysklad_password}".encode()
        ).decode()
        headers["Authorization"] = f"Basic {token}"
    else:
        raise RuntimeError("MoySklad credentials are not configured")
    return headers


async def fetch_assortment(limit: int = 100, offset: int = 0) -> dict:
    settings = get_settings()
    safe_limit = max(1, min(int(limit), 1000))
    safe_offset = max(0, int(offset))
    url = f"{settings.moysklad_base_url.rstrip('/')}/entity/assortment"
    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=10.0)) as client:
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
    if not isinstance(sale_prices, list):
        return 0.0
    for sale_price in sale_prices:
        try:
            value = Decimal(str(sale_price.get("value")))
        except (AttributeError, InvalidOperation, TypeError, ValueError):
            continue
        if value.is_finite() and value > 0:
            return float(
                (value / Decimal("100")).quantize(
                    Decimal("0.01"),
                    rounding=ROUND_HALF_UP,
                )
            )
    return 0.0


def _stock_from_moysklad(row: dict) -> int | None:
    """Return stock only when the provider supplied a finite value."""
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


def _normalized_currency(value: object) -> str:
    currency = str(value or "").strip().upper()
    if not re.fullmatch(r"[A-Z]{3}", currency):
        raise ValueError("MOYSKLAD_DEFAULT_CURRENCY must be a 3-letter code")
    return currency


def _sync_error(exc: Exception) -> str:
    return f"{exc.__class__.__name__}: {exc}"[:2000].strip()


def _start_sync(db: Session, sync_type: str) -> MoySkladSyncLog:
    settings = get_settings()
    now = datetime.utcnow()
    stale_before = now - timedelta(
        minutes=max(10, int(settings.moysklad_sync_interval_minutes) * 2)
    )
    try:
        running = (
            db.query(MoySkladSyncLog)
            .filter(MoySkladSyncLog.status == "started")
            .with_for_update()
            .all()
        )
        for existing in running:
            if existing.created_at and existing.created_at <= stale_before:
                existing.status = "failed"
                existing.finished_at = now
                existing.error = "Recovered stale MoySklad synchronization lease"
            else:
                db.rollback()
                raise MoySkladSyncInProgress("MoySklad synchronization is already running")

        log = MoySkladSyncLog(sync_type=sync_type, status="started", error="")
        db.add(log)
        db.commit()
        db.refresh(log)
        return log
    except MoySkladSyncInProgress:
        raise
    except IntegrityError as exc:
        db.rollback()
        raise MoySkladSyncInProgress(
            "MoySklad synchronization is already running"
        ) from exc
    except Exception:
        db.rollback()
        raise


def _finish_sync_failed(db: Session, log_id: int, exc: Exception) -> MoySkladSyncLog:
    db.rollback()
    log = (
        db.query(MoySkladSyncLog)
        .filter(MoySkladSyncLog.id == log_id)
        .with_for_update()
        .first()
    )
    if not log:
        raise RuntimeError("MoySklad sync log disappeared") from exc
    log.status = "failed"
    log.error = _sync_error(exc) or "MoySklad synchronization failed"
    log.finished_at = datetime.utcnow()
    db.commit()
    db.refresh(log)
    return log


async def _fetch_assortment_snapshot(limit: int) -> list[dict]:
    rows_snapshot: list[dict] = []
    seen_provider_ids: set[str] = set()
    page_signatures: set[tuple[str, ...]] = set()
    offset = 0

    for _page_number in range(MAX_MOYSKLAD_PAGES):
        data = await fetch_assortment(limit=limit, offset=offset)
        rows = data.get("rows", [])
        if not isinstance(rows, list):
            raise ValueError("MoySklad assortment rows must be a list")
        if not rows:
            return rows_snapshot

        page_provider_ids: list[str] = []
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError("MoySklad assortment row must be an object")
            provider_id = str(row.get("id") or "").strip()
            page_provider_ids.append(provider_id)
            if provider_id:
                if provider_id in seen_provider_ids:
                    raise ValueError(
                        f"MoySklad snapshot contains duplicate item id {provider_id}"
                    )
                seen_provider_ids.add(provider_id)
            rows_snapshot.append(row)
            if len(rows_snapshot) > MAX_MOYSKLAD_ROWS:
                raise ValueError("MoySklad assortment exceeds the safe row limit")

        signature = tuple(page_provider_ids)
        if signature in page_signatures:
            raise ValueError("MoySklad pagination returned a repeated page")
        page_signatures.add(signature)

        offset += len(rows)
        if len(rows) < limit:
            return rows_snapshot

    raise ValueError("MoySklad assortment exceeds the safe page limit")


def _resolve_product(
    db: Session,
    *,
    moysklad_id: str,
    sku: str,
) -> tuple[Product | None, str | None]:
    by_provider = (
        db.query(Product).filter(Product.moysklad_id == moysklad_id).first()
        if moysklad_id
        else None
    )
    by_sku = db.query(Product).filter(Product.sku == sku).first()
    if by_provider and by_sku and by_provider.id != by_sku.id:
        return None, "Provider id and SKU resolve to different local products"
    product = by_provider or by_sku
    if product and product.moysklad_id not in {"", moysklad_id}:
        return None, f"SKU already belongs to MoySklad item {product.moysklad_id}"
    if product and product.sku != sku:
        collision = db.query(Product.id).filter(Product.sku == sku).first()
        if collision and collision.id != product.id:
            return None, "Updated MoySklad SKU belongs to another local product"
        product.sku = sku
    return product, None


def _resolve_variant(
    db: Session,
    *,
    product: Product,
    moysklad_id: str,
    sku: str,
) -> tuple[ProductVariant | None, str | None]:
    by_provider = (
        db.query(ProductVariant)
        .filter(ProductVariant.moysklad_id == moysklad_id)
        .first()
        if moysklad_id
        else None
    )
    by_sku = db.query(ProductVariant).filter(ProductVariant.sku == sku).first()
    if by_provider and by_sku and by_provider.id != by_sku.id:
        return None, "Provider id and SKU resolve to different local variants"
    variant = by_provider or by_sku
    if variant and variant.product_id != product.id:
        return None, "Variant identity belongs to another local product"
    if variant and variant.moysklad_id not in {"", moysklad_id}:
        return None, f"Variant SKU belongs to MoySklad item {variant.moysklad_id}"
    if variant and variant.sku != sku:
        collision = db.query(ProductVariant.id).filter(ProductVariant.sku == sku).first()
        if collision and collision.id != variant.id:
            return None, "Updated MoySklad variant SKU belongs to another variant"
        variant.sku = sku
    return variant, None


def _apply_assortment_snapshot(
    db: Session,
    rows: list[dict],
    *,
    currency: str,
) -> tuple[int, int, int]:
    seen = 0
    upserted_products = 0
    upserted_variants = 0

    for row in rows:
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
        if len(moysklad_id) > 255:
            log_conflict(
                db,
                moysklad_id[:255],
                sku[:120],
                "provider_id_too_long",
                "MoySklad item id exceeds 255 characters",
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

        name = str(row.get("name") or sku).strip()[:255] or sku
        price = _price_from_moysklad(row)
        external_stock = _stock_from_moysklad(row)
        product, product_conflict = _resolve_product(
            db,
            moysklad_id=moysklad_id,
            sku=sku,
        )
        if product_conflict:
            log_conflict(
                db,
                moysklad_id,
                sku,
                "product_identity_collision",
                product_conflict,
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
                description=str(row.get("description") or "").strip(),
                price=price,
                currency=currency,
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
            product.description = str(
                row.get("description") or product.description or ""
            ).strip()
            if price > 0:
                product.price = price
                product.currency = currency
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
            upserted_products += 1

        size_source = row.get("size")
        if not size_source:
            uom = row.get("uom")
            size_source = uom.get("name") if isinstance(uom, dict) else None
        raw_size = str(size_source or "ONE SIZE").strip()[:32] or "ONE SIZE"
        size = apply_mapping(db, "size", raw_size, raw_size)
        size = str(size or raw_size).strip()[:32] or raw_size

        variant, variant_conflict = _resolve_variant(
            db,
            product=product,
            moysklad_id=moysklad_id,
            sku=sku,
        )
        if variant_conflict:
            log_conflict(
                db,
                moysklad_id,
                sku,
                "variant_identity_collision",
                variant_conflict,
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
                size=size,
                sku=sku,
                moysklad_id=moysklad_id,
                stock_qty=external_stock if external_stock is not None else 0,
                reserved_qty=0,
            )
            db.add(variant)
            upserted_variants += 1
        else:
            variant.moysklad_id = moysklad_id
            variant.size = size
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

    return seen, upserted_products, upserted_variants


async def sync_assortment_to_catalog(
    db: Session,
    sync_type: str = "manual",
) -> MoySkladSyncLog:
    settings = get_settings()
    normalized_type = str(sync_type or "").strip().lower()
    if normalized_type not in {"manual", "scheduled"}:
        raise ValueError("Invalid MoySklad sync type")

    log = _start_sync(db, normalized_type)
    log_id = log.id
    try:
        limit = max(1, min(int(settings.moysklad_sync_limit), 1000))
        currency = _normalized_currency(settings.moysklad_default_currency)
        rows = await _fetch_assortment_snapshot(limit)

        # No catalog mutation happens until the complete remote snapshot has
        # been fetched and validated. The entire apply then commits once.
        db.rollback()
        log = (
            db.query(MoySkladSyncLog)
            .filter(MoySkladSyncLog.id == log_id)
            .with_for_update()
            .one()
        )
        if log.status != "started":
            raise RuntimeError("MoySklad sync lease is no longer active")

        seen, products, variants = _apply_assortment_snapshot(
            db,
            rows,
            currency=currency,
        )
        log.status = "success"
        log.products_seen = seen
        log.products_upserted = products
        log.variants_upserted = variants
        log.error = ""
        log.finished_at = datetime.utcnow()
        db.commit()
        db.refresh(log)
        return log
    except MoySkladSyncInProgress:
        raise
    except Exception as exc:
        return _finish_sync_failed(db, log_id, exc)
