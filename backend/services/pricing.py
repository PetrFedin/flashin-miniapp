from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Iterable

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ..catalog_models import ProductMerchandising
from ..database import utcnow_naive
from ..models import Product

_MONEY_STEP = Decimal("0.01")


@dataclass(frozen=True)
class ProductPriceQuote:
    product_id: int
    regular_price: Decimal
    effective_price: Decimal
    compare_at_price: Decimal | None
    promo_price: Decimal | None
    promo_active: bool
    sale_starts_at: datetime | None
    sale_ends_at: datetime | None

    def public_payload(self) -> dict[str, object]:
        return {
            "regular_price": float(self.regular_price),
            "effective_price": float(self.effective_price),
            "compare_at_price": float(self.compare_at_price) if self.compare_at_price is not None else None,
            "promo_price": float(self.promo_price) if self.promo_price is not None else None,
            "promo_active": self.promo_active,
            "sale_starts_at": self.sale_starts_at,
            "sale_ends_at": self.sale_ends_at,
        }


def _money(value: object, field: str, *, allow_none: bool = False) -> Decimal | None:
    if value is None and allow_none:
        return None
    try:
        amount = Decimal(str(value)).quantize(_MONEY_STEP, rounding=ROUND_HALF_UP)
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=f"Invalid {field}") from exc
    if not amount.is_finite():
        raise HTTPException(status_code=409, detail=f"Invalid {field}")
    return amount


def _utc_naive(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def quote_product_price(
    product: Product,
    merchandising: ProductMerchandising | None = None,
    *,
    now: datetime | None = None,
) -> ProductPriceQuote:
    regular = _money(product.price, "product price")
    if regular is None or regular <= 0:
        raise HTTPException(status_code=409, detail=f"Invalid price for product {product.id}")

    old_price = _money(product.old_price, "product old price", allow_none=True)
    if old_price is not None and old_price <= 0:
        raise HTTPException(status_code=409, detail=f"Invalid old price for product {product.id}")

    promo = _money(
        merchandising.promo_price if merchandising else None,
        "product promo price",
        allow_none=True,
    )
    if promo is not None and (promo <= 0 or promo >= regular):
        raise HTTPException(
            status_code=409,
            detail=f"Promo price must be lower than regular price for product {product.id}",
        )

    starts_at = _utc_naive(merchandising.sale_starts_at if merchandising else None)
    ends_at = _utc_naive(merchandising.sale_ends_at if merchandising else None)
    if starts_at and ends_at and starts_at >= ends_at:
        raise HTTPException(
            status_code=409,
            detail=f"Invalid sale window for product {product.id}",
        )

    pricing_now = _utc_naive(now) or utcnow_naive()
    promo_active = bool(
        promo is not None
        and (starts_at is None or pricing_now >= starts_at)
        and (ends_at is None or pricing_now < ends_at)
    )
    effective = promo if promo_active and promo is not None else regular
    if promo_active:
        compare_at = regular
    elif old_price is not None and old_price > regular:
        compare_at = old_price
    else:
        compare_at = None

    return ProductPriceQuote(
        product_id=int(product.id),
        regular_price=regular,
        effective_price=effective,
        compare_at_price=compare_at,
        promo_price=promo,
        promo_active=promo_active,
        sale_starts_at=starts_at,
        sale_ends_at=ends_at,
    )


def load_product_price_quotes(
    db: Session,
    products: Iterable[Product],
    *,
    now: datetime | None = None,
    lock: bool = False,
) -> dict[int, ProductPriceQuote]:
    supplied = {int(product.id): product for product in products}
    product_ids = sorted(supplied)
    if not product_ids:
        return {}

    if lock:
        locked_products = (
            db.query(Product)
            .filter(Product.id.in_(product_ids))
            .order_by(Product.id.asc())
            .with_for_update()
            .all()
        )
        supplied = {int(product.id): product for product in locked_products}
        missing = [product_id for product_id in product_ids if product_id not in supplied]
        if missing:
            raise HTTPException(
                status_code=409,
                detail={"message": "Product pricing changed during checkout", "product_ids": missing},
            )

    merch_query = (
        db.query(ProductMerchandising)
        .filter(ProductMerchandising.product_id.in_(product_ids))
        .order_by(ProductMerchandising.product_id.asc())
    )
    if lock:
        merch_query = merch_query.with_for_update()
    merch = {int(row.product_id): row for row in merch_query.all()}

    pricing_now = _utc_naive(now) or utcnow_naive()
    return {
        product_id: quote_product_price(
            supplied[product_id],
            merch.get(product_id),
            now=pricing_now,
        )
        for product_id in product_ids
    }
