from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException

from backend.catalog_models import ProductMerchandising
from backend.models import Product
from backend.services.pricing import quote_product_price


def product(*, price=1000.0, old_price=None) -> Product:
    return Product(
        id=41,
        sku="PRICE-41",
        title="Pricing product",
        slug="pricing-product",
        brand="FLASHIN",
        description="",
        price=price,
        old_price=old_price,
        currency="RUB",
        category="Clothing",
        gender="unisex",
        active=True,
        is_drop=False,
        is_rare=False,
    )


def merch(*, promo_price=None, starts=None, ends=None) -> ProductMerchandising:
    return ProductMerchandising(
        product_id=41,
        availability_status="in_stock",
        promo_price=promo_price,
        sale_starts_at=starts,
        sale_ends_at=ends,
    )


def test_scheduled_promo_is_half_open_and_uses_regular_price_as_compare_at():
    starts = datetime(2026, 8, 17, 10, 0, 0)
    ends = datetime(2026, 8, 17, 12, 0, 0)
    row = merch(promo_price=750.0, starts=starts, ends=ends)

    before = quote_product_price(product(), row, now=starts - timedelta(microseconds=1))
    at_start = quote_product_price(product(), row, now=starts)
    before_end = quote_product_price(product(), row, now=ends - timedelta(microseconds=1))
    at_end = quote_product_price(product(), row, now=ends)

    assert before.effective_price == before.regular_price
    assert before.promo_active is False
    assert at_start.effective_price != at_start.regular_price
    assert at_start.promo_active is True
    assert before_end.promo_active is True
    assert at_end.effective_price == at_end.regular_price
    assert at_end.promo_active is False
    assert at_start.public_payload()["compare_at_price"] == 1000.0


def test_future_promo_configuration_is_private_until_it_is_active():
    row = merch(
        promo_price=700.0,
        starts=datetime(2026, 8, 18, 10, 0, 0),
        ends=datetime(2026, 8, 19, 10, 0, 0),
    )
    quote = quote_product_price(product(), row, now=datetime(2026, 8, 17, 10, 0, 0))

    public = quote.public_payload()
    admin = quote.admin_payload()
    assert public["promo_active"] is False
    assert public["promo_price"] is None
    assert public["sale_ends_at"] is None
    assert admin["configured_promo_price"] == 700.0
    assert admin["sale_starts_at"] == datetime(2026, 8, 18, 10, 0, 0)
    assert admin["sale_ends_at"] == datetime(2026, 8, 19, 10, 0, 0)


def test_promo_without_window_is_active_and_old_price_returns_after_promo_window():
    active = quote_product_price(
        product(price=1000.0, old_price=1200.0),
        merch(promo_price=800.0),
        now=datetime(2026, 8, 17, 10, 0, 0),
    )
    future = quote_product_price(
        product(price=1000.0, old_price=1200.0),
        merch(
            promo_price=800.0,
            starts=datetime(2026, 8, 18, 10, 0, 0),
            ends=datetime(2026, 8, 19, 10, 0, 0),
        ),
        now=datetime(2026, 8, 17, 10, 0, 0),
    )

    assert float(active.effective_price) == 800.0
    assert float(active.compare_at_price) == 1000.0
    assert future.promo_active is False
    assert float(future.effective_price) == 1000.0
    assert float(future.compare_at_price) == 1200.0


def test_timezone_aware_window_is_normalized_to_utc():
    moscow = timezone(timedelta(hours=3))
    row = merch(
        promo_price=900.0,
        starts=datetime(2026, 8, 17, 13, 0, tzinfo=moscow),
        ends=datetime(2026, 8, 17, 14, 0, tzinfo=moscow),
    )
    quote = quote_product_price(
        product(),
        row,
        now=datetime(2026, 8, 17, 10, 30, tzinfo=timezone.utc),
    )

    assert quote.promo_active is True
    assert quote.sale_starts_at == datetime(2026, 8, 17, 10, 0)
    assert quote.sale_ends_at == datetime(2026, 8, 17, 11, 0)


@pytest.mark.parametrize("promo_price", [0, -1, 1000, 1001])
def test_invalid_promo_price_fails_closed(promo_price):
    with pytest.raises(HTTPException) as exc_info:
        quote_product_price(
            product(price=1000),
            merch(promo_price=promo_price),
            now=datetime(2026, 8, 17, 10, 0, 0),
        )
    assert exc_info.value.status_code == 409


def test_invalid_sale_window_fails_closed():
    with pytest.raises(HTTPException) as exc_info:
        quote_product_price(
            product(),
            merch(
                promo_price=800,
                starts=datetime(2026, 8, 17, 12, 0, 0),
                ends=datetime(2026, 8, 17, 12, 0, 0),
            ),
            now=datetime(2026, 8, 17, 10, 0, 0),
        )
    assert exc_info.value.status_code == 409
