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
from .moysklad_stock_authority import evaluate_moysklad_stock_snapshot
from .runtime_capabilities import require_moysklad_execution


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
    settings = require_moysklad_execution(get_settings())
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


async def fetch_stock_by_store(limit: int = 1000, offset: int = 0) -> dict:
    """Fetch the provider stock report that preserves warehouse identity."""
    settings = require_moysklad_execution(get_settings())
    safe_limit = max(1, min(int(limit), 1000))
    safe_offset = max(0, int(offset))
    url = f"{settings.moysklad_base_url}/report/stock/bystore"
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(
            url,
            headers=_headers(),
            params={
                "limit": safe_limit,
                "offset": safe_offset,
                "groupBy": "variant",
            },
        )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError("MoySklad stock-by-store response must be an object")
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


def _sellable_stock_from_store_row(row: dict, store_id: str) -> int:
    """Return stock only from the configured sellable MoySklad store.

    The by-store report includes every warehouse. Damaged and quarantine
    inventory must never be folded back into storefront stock.
    """
    clean_store_id = str(store_id or "").strip()
    if not clean_store_id:
        raise ValueError("Sellable MoySklad store id is required")
    entries = row.get("stockByStore")
    if entries is None:
        return 0
    if not isinstance(entries, list):
        raise ValueError("MoySklad stockByStore must be a list")
    matched = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        if _reference_id(entry) != clean_store_id:
            continue
        try:
            value = float(entry.get("stock", 0))
        except (TypeError, ValueError) as exc:
            raise ValueError("MoySklad sellable-store stock is invalid") from exc
        if not math.isfinite(value):
            raise ValueError("MoySklad sellable-store stock is invalid")
        matched.append(max(int(value), 0))
    if len(matched) > 1:
        raise ValueError("MoySklad sellable store appears more than once in one stock row")
    return matched[0] if matched else 0


async def fetch_sellable_store_stock_snapshot(store_id: str) -> dict[str, int]:
    """Return one complete provider-id -> sellable-store stock snapshot."""
    clean_store_id = str(store_id or "").strip()
    if not clean_store_id:
        raise ValueError("MOYSKLAD_STORE_ID is required for sellable stock authority")

    snapshot: dict[str, int] = {}
    offset = 0
    while True:
        payload = await fetch_stock_by_store(limit=1000, offset=offset)
        rows = payload.get("rows", [])
        if not isinstance(rows, list):
            raise ValueError("MoySklad stock-by-store rows must be a list")
        if not rows:
            break
        for row in rows:
            if not isinstance(row, dict):
                continue
            provider_id = _reference_id(row)
            if not provider_id:
                raise ValueError("MoySklad stock-by-store row has no assortment identity")
            if provider_id in snapshot:
                raise ValueError(
                    f"MoySklad stock-by-store returned duplicate assortment id {provider_id}"
                )
            snapshot[provider_id] = _sellable_stock_from_store_row(row, clean_store_id)
        offset += len(rows)
        if len(rows) < 1000:
            break
    return snapshot


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


class MoySkladIdentityConflict(ValueError):
    """Fail-closed provider identity ambiguity with operator-visible evidence."""

    def __init__(self, conflict_type: str, moysklad_id: str, sku: str, message: str):
        super().__init__(message)
        self.conflict_type = conflict_type
        self.moysklad_id = str(moysklad_id or "").strip()
        self.sku = str(sku or "").strip()[:120]
        self.message = message


def _identity_conflict(
    conflict_type: str,
    moysklad_id: str,
    sku: str,
    message: str,
) -> MoySkladIdentityConflict:
    return MoySkladIdentityConflict(conflict_type, moysklad_id, sku, message)


def _provider_identity_row(
    db: Session,
    model,
    provider_id: str,
    *,
    entity: str,
    sku: str,
):
    rows = (
        db.query(model)
        .filter(model.moysklad_id == provider_id)
        .order_by(model.id.asc())
        .limit(2)
        .all()
    )
    if len(rows) > 1:
        raise _identity_conflict(
            "provider_id_collision",
            provider_id,
            sku,
            f"MoySklad {entity} id {provider_id} is mapped to multiple local rows",
        )
    return rows[0] if rows else None


def _sku_identity_row(db: Session, model, sku: str):
    return db.query(model).filter(model.sku == sku).first()


def _ensure_product_sku(
    db: Session,
    product: Product,
    sku: str,
    *,
    provider_id: str,
) -> None:
    owner = _sku_identity_row(db, Product, sku)
    if owner is not None and int(owner.id) != int(product.id):
        raise _identity_conflict(
            "sku_collision",
            provider_id,
            sku,
            f"Product SKU {sku} already belongs to local product {owner.id}",
        )
    product.sku = sku


def _ensure_variant_sku(
    db: Session,
    variant: ProductVariant,
    sku: str,
    *,
    provider_id: str,
) -> None:
    owner = _sku_identity_row(db, ProductVariant, sku)
    if owner is not None and int(owner.id) != int(variant.id):
        raise _identity_conflict(
            "variant_sku_collision",
            provider_id,
            sku,
            f"Variant SKU {sku} already belongs to local variant {owner.id}",
        )
    variant.sku = sku


def _bind_variant_parent(
    db: Session,
    product: Product,
    *,
    parent_provider_id: str,
    variant_provider_id: str,
    sku: str,
) -> None:
    current_parent = str(product.moysklad_id or "").strip()
    if current_parent and current_parent != parent_provider_id:
        raise _identity_conflict(
            "parent_reassignment",
            variant_provider_id,
            sku,
            (
                f"Variant {variant_provider_id} is attached to parent {current_parent}; "
                f"provider now reports parent {parent_provider_id}"
            ),
        )
    mapped_parent = _provider_identity_row(
        db,
        Product,
        parent_provider_id,
        entity="product",
        sku=sku,
    )
    if mapped_parent is not None and int(mapped_parent.id) != int(product.id):
        raise _identity_conflict(
            "parent_reassignment",
            variant_provider_id,
            sku,
            (
                f"Variant {variant_provider_id} local parent {product.id} conflicts with "
                f"provider parent mapped to local product {mapped_parent.id}"
            ),
        )
    if not current_parent:
        product.moysklad_id = parent_provider_id


def _placeholder_parent_sku(parent_provider_id: str) -> str:
    return f"MS-PARENT-{parent_provider_id}"[:120]


def _resolve_assortment_identity(
    db: Session,
    row: dict,
    sku: str,
) -> tuple[Product | None, ProductVariant | None, str, bool]:
    """Resolve immutable MoySklad IDs before considering mutable SKU attributes.

    Provider IDs are authoritative. SKU may adopt exactly one previously unmapped
    local row, or rename an already mapped row when the target SKU is free.
    """

    row_provider_id = str(row.get("id") or "").strip()
    if not row_provider_id:
        raise _identity_conflict(
            "missing_identity",
            "",
            sku,
            "MoySklad item has no stable provider id",
        )
    if len(row_provider_id) > 255:
        raise _identity_conflict(
            "provider_id_too_long",
            row_provider_id[:255],
            sku,
            "MoySklad provider id exceeds 255 characters",
        )

    is_variant = _row_type(row) == "variant"
    parent_provider_id = (
        _reference_id(row.get("product")) if is_variant else row_provider_id
    )
    if is_variant and not parent_provider_id:
        raise _identity_conflict(
            "missing_parent_identity",
            row_provider_id,
            sku,
            "MoySklad variant has no authoritative parent product id",
        )
    if len(parent_provider_id) > 255:
        raise _identity_conflict(
            "provider_id_too_long",
            row_provider_id,
            sku,
            "MoySklad parent product id exceeds 255 characters",
        )

    mapped_variant = _provider_identity_row(
        db,
        ProductVariant,
        row_provider_id,
        entity="variant",
        sku=sku,
    )

    if is_variant:
        if mapped_variant is not None:
            product = db.get(Product, mapped_variant.product_id)
            if product is None:
                raise _identity_conflict(
                    "broken_parent_link",
                    row_provider_id,
                    sku,
                    "Mapped MoySklad variant points to a missing local product",
                )
            _bind_variant_parent(
                db,
                product,
                parent_provider_id=parent_provider_id,
                variant_provider_id=row_provider_id,
                sku=sku,
            )
            _ensure_variant_sku(
                db,
                mapped_variant,
                sku,
                provider_id=row_provider_id,
            )
            return product, mapped_variant, parent_provider_id, True

        sku_variant = _sku_identity_row(db, ProductVariant, sku)
        if sku_variant is not None:
            current_variant_id = str(sku_variant.moysklad_id or "").strip()
            if current_variant_id and current_variant_id != row_provider_id:
                raise _identity_conflict(
                    "variant_sku_collision",
                    row_provider_id,
                    sku,
                    (
                        f"Variant SKU {sku} already belongs to MoySklad item "
                        f"{current_variant_id}"
                    ),
                )
            product = db.get(Product, sku_variant.product_id)
            if product is None:
                raise _identity_conflict(
                    "broken_parent_link",
                    row_provider_id,
                    sku,
                    "SKU adoption candidate points to a missing local product",
                )
            _bind_variant_parent(
                db,
                product,
                parent_provider_id=parent_provider_id,
                variant_provider_id=row_provider_id,
                sku=sku,
            )
            sku_variant.moysklad_id = row_provider_id
            return product, sku_variant, parent_provider_id, True

        product = _provider_identity_row(
            db,
            Product,
            parent_provider_id,
            entity="product",
            sku=sku,
        )
        return product, None, parent_provider_id, True

    product = _provider_identity_row(
        db,
        Product,
        row_provider_id,
        entity="product",
        sku=sku,
    )
    if product is not None:
        _ensure_product_sku(db, product, sku, provider_id=row_provider_id)
    else:
        sku_product = _sku_identity_row(db, Product, sku)
        if sku_product is not None:
            current_product_id = str(sku_product.moysklad_id or "").strip()
            if current_product_id and current_product_id != row_provider_id:
                raise _identity_conflict(
                    "sku_collision",
                    row_provider_id,
                    sku,
                    (
                        f"Product SKU {sku} already belongs to MoySklad item "
                        f"{current_product_id}"
                    ),
                )
            sku_product.moysklad_id = row_provider_id
            product = sku_product

    if mapped_variant is not None:
        if product is None:
            candidate_product = db.get(Product, mapped_variant.product_id)
            if candidate_product is None:
                raise _identity_conflict(
                    "broken_parent_link",
                    row_provider_id,
                    sku,
                    "Mapped standalone assortment row points to a missing local product",
                )
            _bind_variant_parent(
                db,
                candidate_product,
                parent_provider_id=row_provider_id,
                variant_provider_id=row_provider_id,
                sku=sku,
            )
            _ensure_product_sku(
                db,
                candidate_product,
                sku,
                provider_id=row_provider_id,
            )
            product = candidate_product
        elif int(mapped_variant.product_id) != int(product.id):
            raise _identity_conflict(
                "parent_reassignment",
                row_provider_id,
                sku,
                "Standalone MoySklad assortment identity maps product and variant to different parents",
            )
        _ensure_variant_sku(
            db,
            mapped_variant,
            sku,
            provider_id=row_provider_id,
        )
        return product, mapped_variant, row_provider_id, False

    sku_variant = _sku_identity_row(db, ProductVariant, sku)
    if sku_variant is not None:
        current_variant_id = str(sku_variant.moysklad_id or "").strip()
        if current_variant_id and current_variant_id != row_provider_id:
            raise _identity_conflict(
                "variant_sku_collision",
                row_provider_id,
                sku,
                (
                    f"Variant SKU {sku} already belongs to MoySklad item "
                    f"{current_variant_id}"
                ),
            )
        if product is None:
            candidate_product = db.get(Product, sku_variant.product_id)
            if candidate_product is None:
                raise _identity_conflict(
                    "broken_parent_link",
                    row_provider_id,
                    sku,
                    "SKU adoption candidate points to a missing local product",
                )
            _bind_variant_parent(
                db,
                candidate_product,
                parent_provider_id=row_provider_id,
                variant_provider_id=row_provider_id,
                sku=sku,
            )
            _ensure_product_sku(
                db,
                candidate_product,
                sku,
                provider_id=row_provider_id,
            )
            product = candidate_product
        elif int(sku_variant.product_id) != int(product.id):
            raise _identity_conflict(
                "parent_reassignment",
                row_provider_id,
                sku,
                "SKU adoption would silently re-parent a local variant",
            )
        sku_variant.moysklad_id = row_provider_id
        return product, sku_variant, row_provider_id, False

    return product, None, row_provider_id, False


def _sync_assortment_row(
    db: Session,
    row: dict,
    *,
    settings,
    sync_type: str,
    admin_id: int | None,
    sellable_stock_snapshot: dict[str, int] | None = None,
) -> tuple[int, int]:
    row_provider_id = str(row.get("id") or "").strip()
    raw_sku = row.get("article") or row.get("code")
    sku = str(raw_sku or row_provider_id).strip()
    if not row_provider_id or not sku:
        log_conflict(
            db,
            row_provider_id,
            sku,
            "missing_identity",
            "MoySklad item has no stable id or SKU",
        )
        return 0, 0
    if len(sku) > 120:
        log_conflict(
            db,
            row_provider_id,
            sku[:120],
            "sku_too_long",
            "MoySklad SKU exceeds 120 characters",
        )
        return 0, 0
    if not raw_sku:
        log_conflict(
            db,
            row_provider_id,
            sku,
            "missing_sku",
            "MoySklad item has no article/code; using provider id as SKU",
        )

    try:
        with db.begin_nested():
            product, variant, parent_provider_id, is_variant = _resolve_assortment_identity(
                db,
                row,
                sku,
            )
            product_created = 0
            name = str(row.get("name") or sku).strip()[:255]
            price = _price_from_moysklad(row, settings.moysklad_sale_price_type)
            external_stock = (
                int(sellable_stock_snapshot.get(row_provider_id, 0))
                if sellable_stock_snapshot is not None
                else _stock_from_moysklad(row)
            )
            missing_price_message = (
                f"Configured sale price type '{settings.moysklad_sale_price_type}' is missing or invalid"
                if settings.moysklad_sale_price_type.strip()
                else "No positive sale price was supplied"
            )

            if product is None:
                product_sku = (
                    _placeholder_parent_sku(parent_provider_id) if is_variant else sku
                )
                existing_product_sku = _sku_identity_row(db, Product, product_sku)
                if existing_product_sku is not None:
                    raise _identity_conflict(
                        "parent_placeholder_collision" if is_variant else "sku_collision",
                        row_provider_id,
                        sku,
                        f"Local product SKU {product_sku} is already occupied",
                    )
                if price <= 0:
                    log_conflict(
                        db,
                        row_provider_id,
                        sku,
                        "missing_price",
                        f"Product imported inactive because {missing_price_message.lower()}",
                    )
                product = Product(
                    sku=product_sku,
                    moysklad_id=parent_provider_id,
                    title=name,
                    slug=_unique_slug(db, product_sku, parent_provider_id),
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
                product_created = 1
            else:
                product.title = name
                product.description = str(row.get("description") or product.description or "")
                if price > 0:
                    product.price = price
                else:
                    log_conflict(
                        db,
                        row_provider_id,
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
            if not raw_size and is_variant:
                log_conflict(
                    db,
                    row_provider_id,
                    sku,
                    "missing_size",
                    "Variant has no configured size attribute; ONE SIZE fallback applied",
                )
            size_source = raw_size or "ONE SIZE"
            size = apply_mapping(db, "size", size_source, size_source)
            raw_color = _attribute_value(
                row,
                settings.moysklad_color_attribute_names,
                direct_keys=("color",),
            )
            color = apply_mapping(db, "color", raw_color, "") if raw_color else ""

            variant_upserted = 0
            if variant is None:
                if external_stock is None:
                    log_conflict(
                        db,
                        row_provider_id,
                        sku,
                        "missing_stock",
                        "Provider did not supply stock; new variant starts at zero",
                    )
                variant = ProductVariant(
                    product_id=product.id,
                    size=str(size)[:32],
                    color=str(color)[:64],
                    sku=sku,
                    moysklad_id=row_provider_id,
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
                variant_upserted = 1
            else:
                if int(variant.product_id) != int(product.id):
                    raise _identity_conflict(
                        "parent_reassignment",
                        row_provider_id,
                        sku,
                        "Resolved MoySklad variant no longer belongs to the authoritative parent product",
                    )
                variant.size = str(size)[:32]
                variant.color = str(color)[:64]
                if external_stock is not None:
                    if external_stock < variant.reserved_qty:
                        log_conflict(
                            db,
                            row_provider_id,
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
                    variant_upserted = 1
            return product_created, variant_upserted
    except MoySkladIdentityConflict as exc:
        log_conflict(
            db,
            exc.moysklad_id or row_provider_id,
            exc.sku or sku,
            exc.conflict_type,
            exc.message,
        )
        return 0, 0


def _apply_synced_stock(
    db: Session,
    variant: ProductVariant,
    external_stock: int,
    *,
    sync_type: str,
    admin_id: int | None,
) -> ProductVariant:
    decision = evaluate_moysklad_stock_snapshot(db, variant, int(external_stock))
    target_stock = int(decision.target_stock)
    if decision.blocked or target_stock == int(variant.stock_qty or 0):
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
        sellable_store_id = str(getattr(settings, "moysklad_store_id", "") or "").strip()
        sellable_stock_snapshot = (
            await fetch_sellable_store_stock_snapshot(sellable_store_id)
            if sellable_store_id
            else None
        )
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
                product_count, variant_count = _sync_assortment_row(
                    db,
                    row,
                    settings=settings,
                    sync_type=sync_type,
                    admin_id=admin_id,
                    sellable_stock_snapshot=sellable_stock_snapshot,
                )
                upserted_products += product_count
                upserted_variants += variant_count

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

