from __future__ import annotations

import csv
import hashlib
import io
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from fastapi import HTTPException
from sqlalchemy.orm import Session, selectinload

from ..catalog_integrity import MAX_CATALOG_PRICE
from ..models import Product, ProductVariant

MAX_PRODUCT_CSV_BYTES = 5_000_000
MAX_PRODUCT_CSV_ROWS = 20_000
MAX_PRODUCT_STOCK = 1_000_000_000
_ALLOWED_COLUMNS = frozenset(
    {
        "sku",
        "title",
        "price",
        "currency",
        "category",
        "active",
        "brand",
        "description",
        "gender",
        "old_price",
        "size",
        "color",
        "variant_sku",
        "stock_qty",
    }
)
_REQUIRED_COLUMNS = frozenset({"sku", "title", "price"})
_TRUE_VALUES = frozenset({"1", "true", "yes", "y", "да"})
_FALSE_VALUES = frozenset({"0", "false", "no", "n", "нет"})
_SKU_COMPONENT_RE = re.compile(r"[^A-Za-z0-9._-]+")


@dataclass(frozen=True)
class ProductRow:
    row_number: int
    sku: str
    title: str
    price: Decimal
    currency: str
    category: str
    active: bool
    brand: str
    description: str
    gender: str
    old_price: Decimal | None
    size: str
    color: str
    variant_sku: str
    stock_qty: int


@dataclass(frozen=True)
class ProductImportResult:
    rows: int
    products_created: int
    products_updated: int
    variants_created: int
    variants_updated: int

    def as_dict(self) -> dict[str, int]:
        return {
            "rows": self.rows,
            "products_created": self.products_created,
            "products_updated": self.products_updated,
            "variants_created": self.variants_created,
            "variants_updated": self.variants_updated,
        }


def _error(row_number: int | None, message: str, *, status_code: int = 400) -> HTTPException:
    prefix = f"CSV row {row_number}: " if row_number is not None else ""
    return HTTPException(status_code=status_code, detail=prefix + message)


def _required(value: object, *, row_number: int, field: str, maximum: int) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise _error(row_number, f"{field} is required")
    if len(normalized) > maximum:
        raise _error(row_number, f"{field} is too long")
    return normalized


def _optional(value: object, *, row_number: int, field: str, maximum: int) -> str:
    normalized = str(value or "").strip()
    if len(normalized) > maximum:
        raise _error(row_number, f"{field} is too long")
    return normalized


def _money(
    value: object,
    *,
    row_number: int,
    field: str,
    nullable: bool = False,
) -> Decimal | None:
    raw = str(value or "").strip()
    if not raw and nullable:
        return None
    try:
        amount = Decimal(raw).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError) as exc:
        raise _error(row_number, f"{field} must be a valid amount") from exc
    if not amount.is_finite() or amount < 0 or amount > MAX_CATALOG_PRICE:
        raise _error(
            row_number,
            f"{field} must be between 0 and {MAX_CATALOG_PRICE}",
        )
    return amount


def _boolean(value: object, *, row_number: int, default: bool = True) -> bool:
    raw = str(value or "").strip().lower()
    if not raw:
        return default
    if raw in _TRUE_VALUES:
        return True
    if raw in _FALSE_VALUES:
        return False
    raise _error(row_number, "active must be true or false")


def _stock(value: object, *, row_number: int) -> int:
    raw = str(value or "0").strip()
    try:
        stock = int(raw)
    except (TypeError, ValueError) as exc:
        raise _error(row_number, "stock_qty must be an integer") from exc
    if stock < 0 or stock > MAX_PRODUCT_STOCK:
        raise _error(
            row_number,
            f"stock_qty must be between 0 and {MAX_PRODUCT_STOCK}",
        )
    return stock


def _default_variant_sku(product_sku: str, size: str, color: str, *, single: bool) -> str:
    if single:
        return product_sku
    components = [product_sku, size]
    if color:
        components.append(color)
    raw = "-".join(components)
    normalized = _SKU_COMPONENT_RE.sub("-", raw).strip("-") or product_sku
    if len(normalized) <= 120:
        return normalized
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
    return f"{normalized[:103].rstrip('-')}-{digest}"


def _import_slug(product_sku: str) -> str:
    digest = hashlib.sha256(product_sku.encode("utf-8")).hexdigest()
    return f"csv-{digest}"


def _decode_csv(content: bytes) -> str:
    if len(content) > MAX_PRODUCT_CSV_BYTES:
        raise _error(
            None,
            f"CSV file exceeds {MAX_PRODUCT_CSV_BYTES} bytes",
            status_code=413,
        )
    try:
        return content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise _error(None, "CSV file must be UTF-8 encoded") from exc


def parse_product_csv(content: bytes) -> list[ProductRow]:
    text = _decode_csv(content)
    reader = csv.DictReader(io.StringIO(text, newline=""))
    if reader.fieldnames is None:
        raise _error(None, "CSV header is required")

    headers = [str(value or "").strip().lower() for value in reader.fieldnames]
    if any(not header for header in headers):
        raise _error(None, "CSV header contains an empty column")
    if len(set(headers)) != len(headers):
        raise _error(None, "CSV header contains duplicate columns")
    unknown = sorted(set(headers) - _ALLOWED_COLUMNS)
    missing = sorted(_REQUIRED_COLUMNS - set(headers))
    if unknown:
        raise _error(None, f"Unsupported CSV columns: {', '.join(unknown)}")
    if missing:
        raise _error(None, f"Missing CSV columns: {', '.join(missing)}")
    reader.fieldnames = headers

    raw_rows: list[dict[str, str]] = []
    for row_number, row in enumerate(reader, start=2):
        if row_number - 1 > MAX_PRODUCT_CSV_ROWS:
            raise _error(
                None,
                f"CSV contains more than {MAX_PRODUCT_CSV_ROWS} data rows",
                status_code=413,
            )
        if None in row:
            raise _error(row_number, "CSV row contains more values than the header")
        normalized = {key: str(value or "").strip() for key, value in row.items()}
        if not any(normalized.values()):
            continue
        normalized["__row_number__"] = str(row_number)
        raw_rows.append(normalized)

    if not raw_rows:
        raise _error(None, "CSV contains no product rows")

    product_counts: dict[str, int] = {}
    for raw in raw_rows:
        sku = _required(
            raw.get("sku"),
            row_number=int(raw["__row_number__"]),
            field="sku",
            maximum=120,
        )
        product_counts[sku] = product_counts.get(sku, 0) + 1

    rows: list[ProductRow] = []
    product_facts: dict[str, tuple] = {}
    variant_skus: set[str] = set()
    variant_combinations: set[tuple[str, str, str]] = set()
    for raw in raw_rows:
        row_number = int(raw["__row_number__"])
        sku = _required(raw.get("sku"), row_number=row_number, field="sku", maximum=120)
        title = _required(raw.get("title"), row_number=row_number, field="title", maximum=255)
        price = _money(raw.get("price"), row_number=row_number, field="price")
        currency = _optional(
            raw.get("currency") or "RUB",
            row_number=row_number,
            field="currency",
            maximum=3,
        ).upper()
        if len(currency) != 3 or not currency.isalpha():
            raise _error(row_number, "currency must be a 3-letter code")
        category = _optional(
            raw.get("category") or "Clothing",
            row_number=row_number,
            field="category",
            maximum=120,
        )
        brand = _optional(
            raw.get("brand") or "FLASHIN",
            row_number=row_number,
            field="brand",
            maximum=120,
        )
        gender = _optional(
            raw.get("gender") or "unisex",
            row_number=row_number,
            field="gender",
            maximum=32,
        )
        description = _optional(
            raw.get("description"),
            row_number=row_number,
            field="description",
            maximum=20_000,
        )
        active = _boolean(raw.get("active"), row_number=row_number)
        old_price = _money(
            raw.get("old_price"),
            row_number=row_number,
            field="old_price",
            nullable=True,
        )
        if active and price <= 0:
            raise _error(row_number, "active product price must be positive")
        if old_price is not None and old_price <= price:
            raise _error(row_number, "old_price must be greater than price")
        size = _optional(
            raw.get("size") or "ONE SIZE",
            row_number=row_number,
            field="size",
            maximum=32,
        )
        color = _optional(
            raw.get("color"),
            row_number=row_number,
            field="color",
            maximum=64,
        )
        variant_sku = _optional(
            raw.get("variant_sku"),
            row_number=row_number,
            field="variant_sku",
            maximum=120,
        ) or _default_variant_sku(
            sku,
            size,
            color,
            single=product_counts[sku] == 1,
        )
        stock_qty = _stock(raw.get("stock_qty"), row_number=row_number)

        facts = (
            title,
            price,
            currency,
            category,
            active,
            brand,
            description,
            gender,
            old_price,
        )
        previous_facts = product_facts.setdefault(sku, facts)
        if previous_facts != facts:
            raise _error(
                row_number,
                "rows for the same sku contain conflicting product fields",
            )
        if variant_sku in variant_skus:
            raise _error(row_number, "variant_sku is duplicated in the CSV")
        combination = (sku, size, color)
        if combination in variant_combinations:
            raise _error(row_number, "size and color are duplicated for the product")
        variant_skus.add(variant_sku)
        variant_combinations.add(combination)

        rows.append(
            ProductRow(
                row_number=row_number,
                sku=sku,
                title=title,
                price=price,
                currency=currency,
                category=category,
                active=active,
                brand=brand,
                description=description,
                gender=gender,
                old_price=old_price,
                size=size,
                color=color,
                variant_sku=variant_sku,
                stock_qty=stock_qty,
            )
        )
    return rows


def import_products_csv(db: Session, content: bytes) -> ProductImportResult:
    rows = parse_product_csv(content)
    product_skus = sorted({row.sku for row in rows})
    variant_skus = sorted({row.variant_sku for row in rows})

    products = (
        db.query(Product)
        .options(selectinload(Product.variants))
        .filter(Product.sku.in_(product_skus))
        .with_for_update()
        .all()
    )
    product_by_sku = {product.sku: product for product in products}
    existing_variants = (
        db.query(ProductVariant)
        .filter(ProductVariant.sku.in_(variant_skus))
        .with_for_update()
        .all()
    )
    variant_by_sku = {variant.sku: variant for variant in existing_variants}

    generated_slugs = {_import_slug(sku): sku for sku in product_skus if sku not in product_by_sku}
    slug_conflicts = (
        db.query(Product.slug, Product.sku)
        .filter(Product.slug.in_(list(generated_slugs)))
        .all()
        if generated_slugs
        else []
    )
    for slug, existing_sku in slug_conflicts:
        if generated_slugs[slug] != existing_sku:
            raise HTTPException(status_code=409, detail="Generated product slug conflicts with existing catalog data")

    grouped: dict[str, list[ProductRow]] = {}
    for row in rows:
        grouped.setdefault(row.sku, []).append(row)

    products_created = 0
    products_updated = 0
    variants_created = 0
    variants_updated = 0

    for sku in product_skus:
        product_rows = grouped[sku]
        first = product_rows[0]
        product = product_by_sku.get(sku)
        if product is None:
            product = Product(sku=sku, slug=_import_slug(sku))
            db.add(product)
            product_by_sku[sku] = product
            products_created += 1
        else:
            products_updated += 1

        product.title = first.title
        product.price = first.price
        product.currency = first.currency
        product.category = first.category
        product.active = first.active
        product.brand = first.brand
        product.description = first.description
        product.gender = first.gender
        product.old_price = first.old_price

        existing_by_combo = {
            (variant.size, variant.color): variant
            for variant in list(product.variants)
        }
        for row in product_rows:
            variant = variant_by_sku.get(row.variant_sku)
            combination_variant = existing_by_combo.get((row.size, row.color))
            if variant is not None and variant.product_id not in {None, product.id}:
                raise _error(
                    row.row_number,
                    "variant_sku belongs to another product",
                    status_code=409,
                )
            if combination_variant is not None and combination_variant.sku != row.variant_sku:
                raise _error(
                    row.row_number,
                    "existing size/color has a different variant_sku",
                    status_code=409,
                )
            if variant is None:
                variant = ProductVariant(
                    product=product,
                    sku=row.variant_sku,
                    size=row.size,
                    color=row.color,
                    stock_qty=row.stock_qty,
                    reserved_qty=0,
                )
                db.add(variant)
                variant_by_sku[row.variant_sku] = variant
                existing_by_combo[(row.size, row.color)] = variant
                variants_created += 1
            else:
                if variant.reserved_qty > row.stock_qty:
                    raise _error(
                        row.row_number,
                        "stock_qty cannot be lower than current reservations",
                        status_code=409,
                    )
                variant.size = row.size
                variant.color = row.color
                variant.stock_qty = row.stock_qty
                variants_updated += 1

    db.flush()
    return ProductImportResult(
        rows=len(rows),
        products_created=products_created,
        products_updated=products_updated,
        variants_created=variants_created,
        variants_updated=variants_updated,
    )
