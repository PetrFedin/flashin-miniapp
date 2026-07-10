import base64
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
        token = base64.b64encode(f"{settings.moysklad_login}:{settings.moysklad_password}".encode()).decode()
        headers["Authorization"] = f"Basic {token}"
    return headers


async def fetch_assortment(limit: int = 100, offset: int = 0) -> dict:
    settings = get_settings()
    url = f"{settings.moysklad_base_url}/entity/assortment"
    async with httpx.AsyncClient(timeout=30) as client:
        res = await client.get(url, headers=_headers(), params={"limit": limit, "offset": offset})
    res.raise_for_status()
    return res.json()


def _price_from_moysklad(row: dict) -> float:
    sale_prices = row.get("salePrices") or []
    if not sale_prices:
        return 0
    value = sale_prices[0].get("value") or 0
    return round(value / 100, 2)


def _stock_from_moysklad(row: dict) -> int:
    # Depending on endpoint, stock may be present as quantity, stock or effectiveStock.
    for key in ("quantity", "stock", "effectiveStock"):
        if key in row and row[key] is not None:
            try:
                return int(row[key])
            except Exception:
                return 0
    return 0


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
            if not rows:
                break
            for row in rows:
                seen += 1
                ms_id = row.get("id", "")
                sku = row.get("article") or row.get("code") or ms_id
                if not sku:
                    continue
                name = row.get("name") or sku
                if not row.get("article") and not row.get("code"):
                    log_conflict(db, ms_id, sku, "missing_sku", "MoySklad item has no article/code; using id as sku")
                product = db.query(Product).filter(Product.sku == sku).first()
                if not product:
                    product = Product(
                        sku=sku,
                        moysklad_id=ms_id,
                        title=name,
                        slug=sku.lower().replace(" ", "-"),
                        brand="FLASHIN",
                        description=row.get("description", "") or "",
                        price=_price_from_moysklad(row),
                        currency=settings.moysklad_default_currency,
                        category=apply_mapping(db, "category", row.get("pathName", "Clothing"), "Clothing"),
                        active=True,
                    )
                    db.add(product)
                    db.flush()
                    upserted_products += 1
                else:
                    product.moysklad_id = ms_id
                    product.title = name
                    product.description = row.get("description", product.description) or ""
                    product.price = _price_from_moysklad(row) or product.price
                    product.category = apply_mapping(db, "category", row.get("pathName", product.category), product.category)

                size = apply_mapping(db, "size", row.get("size") or row.get("uom", {}).get("name") or "ONE SIZE", "ONE SIZE")
                variant_sku = sku
                variant = db.query(ProductVariant).filter(ProductVariant.sku == variant_sku).first()
                if not variant:
                    variant = ProductVariant(
                        product_id=product.id,
                        size=size,
                        sku=variant_sku,
                        moysklad_id=ms_id,
                        stock_qty=_stock_from_moysklad(row),
                        reserved_qty=0,
                    )
                    db.add(variant)
                    upserted_variants += 1
                else:
                    variant.moysklad_id = ms_id
                    variant.stock_qty = max(_stock_from_moysklad(row), variant.reserved_qty)
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
        log.status = "failed"
        log.error = str(exc)
        log.finished_at = datetime.utcnow()
        db.commit()
        return log
