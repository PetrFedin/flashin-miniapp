import base64
import math
import re
from dataclasses import dataclass
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



@dataclass(frozen=True)
class MoySkladAssortmentIdentity:
    entity_type: str
    entity_id: str
    product_provider_id: str
    sku: str
    explicit_sku: bool

    @property
    def is_variant(self) -> bool:
        return self.entity_type == "variant"


def _assortment_identity(row: dict) -> MoySkladAssortmentIdentity:
    entity_id = str(row.get("id") or "").strip()
    raw_sku = row.get("article") or row.get("code")
    sku = str(raw_sku or entity_id).strip()
    entity_type = _row_type(row)
    product_provider_id = (
        _reference_id(row.get("product"))
        if entity_type == "variant"
        else entity_id
    )
    return MoySkladAssortmentIdentity(
        entity_type=entity_type,
        entity_id=entity_id,
        product_provider_id=product_provider_id,
        sku=sku,
        explicit_sku=bool(raw_sku),
    )


def _product_has_provider_variants(
    row: dict,
    *,
    page_variant_parent_ids: set[str] | None = None,
) -> bool:
    """Return True when the provider product is a variant container.

    MoySklad Product exposes a read-only variantsCount. The page-local parent
    references are a defensive fallback for assortment payloads where that
    field is absent.
    """

    try:
        variants_count = int(row.get("variantsCount") or 0)
    except (TypeError, ValueError):
        variants_count = 0
    if variants_count > 0:
        return True
    provider_id = str(row.get("id") or "").strip()
    return bool(
        provider_id
        and page_variant_parent_ids
        and provider_id in page_variant_parent_ids
    )


def _product_by_provider_id(db: Session, provider_id: str) -> Product | None:
    clean = str(provider_id or "").strip()
    if not clean:
        return None
    return db.query(Product).filter(Product.moysklad_id == clean).first()


def _variant_by_provider_id(db: Session, provider_id: str) -> ProductVariant | None:
    clean = str(provider_id or "").strip()
    if not clean:
        return None
    return (
        db.query(ProductVariant)
        .filter(ProductVariant.moysklad_id == clean)
        .first()
    )


def _product_sku_is_available(
    db: Session,
    sku: str,
    *,
    current_product_id: int | None,
) -> bool:
    owner = db.query(Product).filter(Product.sku == sku).first()
    return owner is None or int(owner.id) == int(current_product_id or 0)


def _variant_sku_is_available(
    db: Session,
    sku: str,
    *,
    current_variant_id: int | None,
) -> bool:
    owner = db.query(ProductVariant).filter(ProductVariant.sku == sku).first()
    return owner is None or int(owner.id) == int(current_variant_id or 0)


def _resolve_product_authority(
    db: Session,
    *,
    identity: MoySkladAssortmentIdentity,
    row: dict,
    price: float,
    currency: str,
    sale_price_type: str,
) -> tuple[Product | None, bool]:
    """Resolve the local product by immutable provider identity.

    Variant rows may only attach to an already mapped parent product. They are
    never allowed to manufacture a Product from the variant's own provider ID.
    Standalone product rows may adopt one unmapped local SKU exactly once.
    """

    if identity.is_variant:
        if not identity.product_provider_id:
            log_conflict(
                db,
                identity.entity_id,
                identity.sku[:120],
                "variant_missing_parent_identity",
                "MoySklad variant has no parent product provider identity",
            )
            return None, False
        product = _product_by_provider_id(db, identity.product_provider_id)
        if product is None:
            log_conflict(
                db,
                identity.entity_id,
                identity.sku[:120],
                "variant_parent_not_mapped",
                (
                    "MoySklad variant parent product "
                    f"{identity.product_provider_id} is not mapped locally; "
                    "variant was not imported"
                ),
            )
            return None, False
        return product, False

    product = _product_by_provider_id(db, identity.product_provider_id)
    sku_owner = db.query(Product).filter(Product.sku == identity.sku).first()

    if product is None and sku_owner is not None:
        if (
            str(sku_owner.moysklad_id or "").strip()
            and str(sku_owner.moysklad_id).strip() != identity.product_provider_id
        ):
            log_conflict(
                db,
                identity.entity_id,
                identity.sku[:120],
                "product_sku_identity_collision",
                (
                    f"SKU {identity.sku} is already mapped to MoySklad product "
                    f"{sku_owner.moysklad_id}"
                ),
            )
            return None, False
        product = sku_owner
        product.moysklad_id = identity.product_provider_id

    if product is not None and not _product_sku_is_available(
        db,
        identity.sku,
        current_product_id=product.id,
    ):
        log_conflict(
            db,
            identity.entity_id,
            identity.sku[:120],
            "product_sku_identity_collision",
            (
                f"MoySklad product {identity.product_provider_id} cannot adopt SKU "
                f"{identity.sku}; another local Product already owns it"
            ),
        )
        return None, False

    missing_price_message = (
        f"Configured sale price type '{sale_price_type}' is missing or invalid"
        if str(sale_price_type or "").strip()
        else "No positive sale price was supplied"
    )
    created = False
    if product is None:
        if price <= 0:
            log_conflict(
                db,
                identity.entity_id,
                identity.sku[:120],
                "missing_price",
                f"Product imported inactive because {missing_price_message.lower()}",
            )
        product = Product(
            sku=identity.sku,
            moysklad_id=identity.product_provider_id,
            title=str(row.get("name") or identity.sku).strip()[:255],
            slug=_unique_slug(db, identity.sku, identity.product_provider_id),
            brand="FLASHIN",
            description=str(row.get("description") or ""),
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
        created = True
    else:
        product.sku = identity.sku
        product.title = str(row.get("name") or identity.sku).strip()[:255]
        product.description = str(row.get("description") or product.description or "")
        if price > 0:
            product.price = price
        else:
            log_conflict(
                db,
                identity.entity_id,
                identity.sku[:120],
                "missing_price",
                f"Existing local price preserved because {missing_price_message.lower()}",
            )
        product.category = apply_mapping(
            db,
            "category",
            row.get("pathName", product.category),
            product.category,
        )
    return product, created


def _resolve_variant_authority(
    db: Session,
    *,
    identity: MoySkladAssortmentIdentity,
    product: Product,
    size: str,
    color: str,
) -> tuple[ProductVariant | None, bool]:
    """Resolve one sellable local variant by immutable assortment entity ID."""

    variant = _variant_by_provider_id(db, identity.entity_id)
    sku_owner = db.query(ProductVariant).filter(ProductVariant.sku == identity.sku).first()

    if variant is not None and int(variant.product_id) != int(product.id):
        log_conflict(
            db,
            identity.entity_id,
            identity.sku[:120],
            "variant_parent_identity_conflict",
            (
                f"MoySklad variant {identity.entity_id} is already bound to local "
                f"product {variant.product_id}, not authoritative parent {product.id}"
            ),
        )
        return None, False

    if variant is None and sku_owner is not None:
        owner_provider_id = str(sku_owner.moysklad_id or "").strip()
        if owner_provider_id and owner_provider_id != identity.entity_id:
            log_conflict(
                db,
                identity.entity_id,
                identity.sku[:120],
                "variant_sku_identity_collision",
                (
                    f"Variant SKU {identity.sku} is already mapped to MoySklad "
                    f"assortment {owner_provider_id}"
                ),
            )
            return None, False
        if int(sku_owner.product_id) != int(product.id):
            log_conflict(
                db,
                identity.entity_id,
                identity.sku[:120],
                "variant_parent_identity_conflict",
                "Unmapped local variant SKU belongs to another local product",
            )
            return None, False
        variant = sku_owner
        variant.moysklad_id = identity.entity_id

    if variant is not None and not _variant_sku_is_available(
        db,
        identity.sku,
        current_variant_id=variant.id,
    ):
        log_conflict(
            db,
            identity.entity_id,
            identity.sku[:120],
            "variant_sku_identity_collision",
            (
                f"MoySklad assortment {identity.entity_id} cannot adopt SKU "
                f"{identity.sku}; another local ProductVariant already owns it"
            ),
        )
        return None, False

    created = False
    if variant is None:
        dimension_owner = (
            db.query(ProductVariant)
            .filter(
                ProductVariant.product_id == int(product.id),
                ProductVariant.size == str(size)[:32],
                ProductVariant.color == str(color)[:64],
            )
            .first()
        )
        if dimension_owner is not None:
            log_conflict(
                db,
                identity.entity_id,
                identity.sku[:120],
                "variant_dimension_identity_collision",
                (
                    "Authoritative parent already has another local variant for "
                    f"size={str(size)[:32]!r}, color={str(color)[:64]!r}"
                ),
            )
            return None, False
        variant = ProductVariant(
            product_id=product.id,
            size=str(size)[:32],
            color=str(color)[:64],
            sku=identity.sku,
            moysklad_id=identity.entity_id,
            stock_qty=0,
            reserved_qty=0,
        )
        db.add(variant)
        db.flush()
        created = True
    else:
        variant.sku = identity.sku
        variant.size = str(size)[:32]
        variant.color = str(color)[:64]
    return variant, created

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
        offset = 0
        seen = upserted_products = upserted_variants = 0
        while True:
            data = await fetch_assortment(limit=settings.moysklad_sync_limit, offset=offset)
            rows = data.get("rows", [])
            if not isinstance(rows, list):
                raise ValueError("MoySklad assortment rows must be a list")
            if not rows:
                break

            # Process parent products before variants within each page. Provider
            # identity remains authoritative across pages; an unresolved parent
            # fails closed and will converge on a later sync after the product is mapped.
            page_variant_parent_ids = {
                _reference_id(row.get("product"))
                for row in rows
                if isinstance(row, dict) and _row_type(row) == "variant"
            }
            page_variant_parent_ids.discard("")
            ordered_rows = sorted(
                (row for row in rows if isinstance(row, dict)),
                key=lambda row: _row_type(row) == "variant",
            )
            for row in ordered_rows:
                seen += 1
                identity = _assortment_identity(row)
                if not identity.entity_id or not identity.sku:
                    log_conflict(
                        db,
                        identity.entity_id,
                        identity.sku[:120],
                        "missing_identity",
                        "MoySklad item has no stable id or SKU",
                    )
                    continue
                if len(identity.sku) > 120:
                    log_conflict(
                        db,
                        identity.entity_id,
                        identity.sku[:120],
                        "sku_too_long",
                        "MoySklad SKU exceeds 120 characters",
                    )
                    continue
                if not identity.explicit_sku:
                    log_conflict(
                        db,
                        identity.entity_id,
                        identity.sku,
                        "missing_sku",
                        "MoySklad item has no article/code; using id as SKU",
                    )

                price = _price_from_moysklad(row, settings.moysklad_sale_price_type)
                external_stock = _stock_from_moysklad(row)
                product, product_created = _resolve_product_authority(
                    db,
                    identity=identity,
                    row=row,
                    price=price,
                    currency=settings.moysklad_default_currency,
                    sale_price_type=settings.moysklad_sale_price_type,
                )
                if product is None:
                    continue
                if product_created:
                    upserted_products += 1

                if (
                    not identity.is_variant
                    and _product_has_provider_variants(
                        row,
                        page_variant_parent_ids=page_variant_parent_ids,
                    )
                ):
                    # The Product row is the parent style/container. Sellable
                    # local variants are sourced only from provider variant IDs.
                    continue

                raw_size = _attribute_value(
                    row,
                    settings.moysklad_size_attribute_names,
                    direct_keys=("size",),
                )
                if not raw_size and identity.is_variant:
                    log_conflict(
                        db,
                        identity.entity_id,
                        identity.sku,
                        "missing_size",
                        "Variant has no configured size attribute; ONE SIZE fallback applied",
                    )
                size = apply_mapping(db, "size", raw_size, raw_size) if raw_size else "ONE SIZE"
                raw_color = _attribute_value(
                    row,
                    settings.moysklad_color_attribute_names,
                    direct_keys=("color",),
                )
                color = apply_mapping(db, "color", raw_color, "") if raw_color else ""

                variant, variant_created = _resolve_variant_authority(
                    db,
                    identity=identity,
                    product=product,
                    size=size,
                    color=color,
                )
                if variant is None:
                    continue

                variant_touched = variant_created
                if external_stock is not None:
                    if external_stock < int(variant.reserved_qty or 0):
                        log_conflict(
                            db,
                            identity.entity_id,
                            identity.sku,
                            "stock_below_reserved",
                            "External stock is below the local reserved quantity; reservation preserved",
                        )
                    before_stock = int(variant.stock_qty or 0)
                    variant = _apply_synced_stock(
                        db,
                        variant,
                        external_stock,
                        sync_type=sync_type,
                        admin_id=admin_id,
                    )
                    variant_touched = variant_touched or int(variant.stock_qty or 0) != before_stock
                if variant_touched:
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
