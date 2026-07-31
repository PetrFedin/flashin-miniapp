import math
import re
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from fastapi import HTTPException
from sqlalchemy import CheckConstraint, Index, event, text

from .models import MoySkladSyncLog, Product, ProductVariant

MAX_CATALOG_PRICE = Decimal("1000000000.00")
MAX_SYNC_ERROR_LENGTH = 2000
VALID_MOYSKLAD_SYNC_STATUSES = frozenset({"started", "success", "failed"})
VALID_MOYSKLAD_SYNC_TYPES = frozenset({"manual", "scheduled"})
_CURRENCY_RE = re.compile(r"^[A-Z]{3}$")


def _constraint_names(table) -> set[str]:
    return {constraint.name for constraint in table.constraints if constraint.name}


def _index_names(table) -> set[str]:
    return {index.name for index in table.indexes if index.name}


def _check(table, name: str, expression: str) -> None:
    if name not in _constraint_names(table):
        table.append_constraint(CheckConstraint(expression, name=name))


def _partial_unique_index(table, name: str, columns: list, where: str) -> None:
    if name in _index_names(table):
        return
    predicate = text(where)
    Index(
        name,
        *columns,
        unique=True,
        postgresql_where=predicate,
        sqlite_where=predicate,
    )


def _normalize_required(value: object, *, field: str, maximum: int) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise HTTPException(status_code=400, detail=f"{field} is required")
    if len(normalized) > maximum:
        raise HTTPException(status_code=400, detail=f"{field} is too long")
    return normalized


def _normalize_optional(value: object, *, field: str, maximum: int) -> str:
    normalized = str(value or "").strip()
    if len(normalized) > maximum:
        raise HTTPException(status_code=400, detail=f"{field} is too long")
    return normalized


def _money(value: object, *, field: str, nullable: bool = False) -> float | None:
    if value is None and nullable:
        return None
    try:
        amount = Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=f"{field} must be a valid amount") from exc
    if not amount.is_finite() or amount < 0 or amount > MAX_CATALOG_PRICE:
        raise HTTPException(
            status_code=400,
            detail=f"{field} must be between 0 and {MAX_CATALOG_PRICE}",
        )
    return float(amount)


def _validate_product_before_write(_mapper, _connection, target: Product) -> None:
    target.sku = _normalize_required(target.sku, field="Product SKU", maximum=120)
    target.moysklad_id = _normalize_optional(
        target.moysklad_id,
        field="MoySklad product id",
        maximum=255,
    )
    target.title = _normalize_required(target.title, field="Product title", maximum=255)
    target.slug = _normalize_required(target.slug, field="Product slug", maximum=255)
    target.brand = _normalize_required(target.brand, field="Product brand", maximum=120)
    target.category = _normalize_required(
        target.category,
        field="Product category",
        maximum=120,
    )
    target.gender = _normalize_required(target.gender, field="Product gender", maximum=32)
    target.currency = str(target.currency or "").strip().upper()
    if not _CURRENCY_RE.fullmatch(target.currency):
        raise HTTPException(status_code=400, detail="Product currency must be a 3-letter code")

    target.price = _money(target.price, field="Product price")
    target.old_price = _money(target.old_price, field="Product old price", nullable=True)
    if target.active and target.price <= 0:
        raise HTTPException(status_code=400, detail="Active product price must be positive")
    if target.old_price is not None and target.old_price <= target.price:
        raise HTTPException(
            status_code=400,
            detail="Product old price must be greater than the current price",
        )


def _validate_variant_before_write(_mapper, _connection, target: ProductVariant) -> None:
    target.sku = _normalize_required(target.sku, field="Variant SKU", maximum=120)
    target.moysklad_id = _normalize_optional(
        target.moysklad_id,
        field="MoySklad variant id",
        maximum=255,
    )
    target.size = _normalize_required(target.size, field="Variant size", maximum=32)
    target.color = _normalize_optional(target.color, field="Variant color", maximum=64)
    try:
        target.stock_qty = int(target.stock_qty or 0)
        target.reserved_qty = int(target.reserved_qty or 0)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="Variant stock must be an integer") from exc
    if target.stock_qty < 0 or target.reserved_qty < 0:
        raise HTTPException(status_code=400, detail="Variant stock cannot be negative")
    if target.reserved_qty > target.stock_qty:
        raise HTTPException(status_code=400, detail="Reserved stock cannot exceed stock")


def _validate_sync_log_before_write(_mapper, _connection, target: MoySkladSyncLog) -> None:
    target.sync_type = str(target.sync_type or "").strip().lower()
    target.status = str(target.status or "").strip().lower()
    target.error = str(target.error or "").strip()[:MAX_SYNC_ERROR_LENGTH]
    if target.sync_type not in VALID_MOYSKLAD_SYNC_TYPES:
        raise HTTPException(status_code=400, detail="Invalid MoySklad sync type")
    if target.status not in VALID_MOYSKLAD_SYNC_STATUSES:
        raise HTTPException(status_code=400, detail="Invalid MoySklad sync status")

    for field in ("products_seen", "products_upserted", "variants_upserted"):
        try:
            value = int(getattr(target, field) or 0)
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=f"{field} must be an integer") from exc
        if value < 0:
            raise HTTPException(status_code=400, detail=f"{field} cannot be negative")
        setattr(target, field, value)

    if target.status == "started":
        if target.finished_at is not None or target.error:
            raise HTTPException(status_code=400, detail="Started sync cannot be finished")
    elif target.status == "success":
        if target.finished_at is None or target.error:
            raise HTTPException(status_code=400, detail="Successful sync state is inconsistent")
    elif target.finished_at is None or not target.error:
        raise HTTPException(status_code=400, detail="Failed sync requires finish time and error")


def _register_validation() -> None:
    listeners = (
        (Product, _validate_product_before_write),
        (ProductVariant, _validate_variant_before_write),
        (MoySkladSyncLog, _validate_sync_log_before_write),
    )
    for model, listener in listeners:
        for event_name in ("before_insert", "before_update"):
            if not event.contains(model, event_name, listener):
                event.listen(model, event_name, listener)


def apply_catalog_constraints() -> None:
    product = Product.__table__
    variant = ProductVariant.__table__
    sync_log = MoySkladSyncLog.__table__

    for column, maximum in (
        ("sku", 120),
        ("title", 255),
        ("slug", 255),
        ("brand", 120),
        ("category", 120),
        ("gender", 32),
    ):
        _check(
            product,
            f"ck_products_{column}_normalized",
            f"{column} = trim({column})",
        )
        _check(
            product,
            f"ck_products_{column}_size",
            f"length({column}) BETWEEN 1 AND {maximum}",
        )
    _check(product, "ck_products_moysklad_id_normalized", "moysklad_id = trim(moysklad_id)")
    _check(product, "ck_products_moysklad_id_size", "length(moysklad_id) <= 255")
    _check(product, "ck_products_price_range", f"price BETWEEN 0 AND {MAX_CATALOG_PRICE}")
    _check(product, "ck_products_active_price_positive", "NOT active OR price > 0")
    _check(
        product,
        "ck_products_old_price_coherent",
        f"old_price IS NULL OR (old_price > price AND old_price <= {MAX_CATALOG_PRICE})",
    )
    _check(product, "ck_products_currency_normalized", "currency = upper(trim(currency))")
    _check(product, "ck_products_currency_size", "length(currency) = 3")

    for column, maximum in (("sku", 120), ("size", 32)):
        _check(
            variant,
            f"ck_product_variants_{column}_normalized",
            f"{column} = trim({column})",
        )
        _check(
            variant,
            f"ck_product_variants_{column}_size",
            f"length({column}) BETWEEN 1 AND {maximum}",
        )
    _check(variant, "ck_product_variants_color_normalized", "color = trim(color)")
    _check(variant, "ck_product_variants_color_size", "length(color) <= 64")
    _check(
        variant,
        "ck_product_variants_moysklad_id_normalized",
        "moysklad_id = trim(moysklad_id)",
    )
    _check(variant, "ck_product_variants_moysklad_id_size", "length(moysklad_id) <= 255")

    _partial_unique_index(
        product,
        "uq_products_moysklad_id",
        [product.c.moysklad_id],
        "moysklad_id <> ''",
    )
    _partial_unique_index(
        variant,
        "uq_product_variants_moysklad_id",
        [variant.c.moysklad_id],
        "moysklad_id <> ''",
    )

    _check(
        sync_log,
        "ck_moysklad_sync_logs_type_valid",
        "sync_type IN ('manual', 'scheduled')",
    )
    _check(
        sync_log,
        "ck_moysklad_sync_logs_status_valid",
        "status IN ('started', 'success', 'failed')",
    )
    _check(
        sync_log,
        "ck_moysklad_sync_logs_counts_nonnegative",
        "products_seen >= 0 AND products_upserted >= 0 AND variants_upserted >= 0",
    )
    _check(
        sync_log,
        "ck_moysklad_sync_logs_error_size",
        f"length(error) <= {MAX_SYNC_ERROR_LENGTH}",
    )
    _check(
        sync_log,
        "ck_moysklad_sync_logs_state_coherent",
        "((status = 'started' AND finished_at IS NULL AND error = '') "
        "OR (status = 'success' AND finished_at IS NOT NULL AND error = '') "
        "OR (status = 'failed' AND finished_at IS NOT NULL AND length(trim(error)) > 0))",
    )
    _partial_unique_index(
        sync_log,
        "uq_moysklad_sync_logs_single_started",
        [sync_log.c.status],
        "status = 'started'",
    )


apply_catalog_constraints()
_register_validation()
