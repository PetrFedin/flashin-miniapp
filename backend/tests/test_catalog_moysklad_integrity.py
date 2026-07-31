import asyncio
import inspect
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from backend.api import products as products_api
from backend.database import Base
from backend.models import MoySkladSyncLog, Product, ProductVariant
from backend.services import moysklad


def _session():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _product(**overrides) -> Product:
    values = {
        "sku": "PRODUCT-1",
        "moysklad_id": "provider-product-1",
        "title": "Product",
        "slug": "product-1",
        "brand": "FLASHIN",
        "description": "",
        "price": 1000,
        "old_price": None,
        "currency": "RUB",
        "category": "Clothing",
        "gender": "unisex",
        "active": True,
    }
    values.update(overrides)
    return Product(**values)


def _settings(**overrides):
    values = {
        "moysklad_sync_interval_minutes": 30,
        "moysklad_sync_limit": 100,
        "moysklad_default_currency": "RUB",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_product_write_route_requires_admin_dependency():
    route = next(
        route
        for route in products_api.router.routes
        if route.path == "/products" and "POST" in route.methods
    )
    dependency_names = {
        dependency.call.__name__
        for dependency in route.dependant.dependencies
        if dependency.call is not None
    }

    assert "get_current_admin" in dependency_names


def test_orm_normalizes_catalog_fields_and_money():
    db = _session()
    product = _product(
        sku=" PRODUCT-1 ",
        moysklad_id=" provider-product-1 ",
        title=" Product ",
        slug=" product-1 ",
        brand=" FLASHIN ",
        price="1000.005",
        old_price="1200.004",
        currency=" rub ",
        category=" Clothing ",
        gender=" unisex ",
    )
    db.add(product)
    db.commit()

    assert product.sku == "PRODUCT-1"
    assert product.moysklad_id == "provider-product-1"
    assert product.price == 1000.01
    assert product.old_price == 1200.0
    assert product.currency == "RUB"


def test_orm_rejects_invalid_active_price_currency_and_old_price():
    for product in (
        _product(sku="ZERO", slug="zero", moysklad_id="zero", price=0),
        _product(sku="FX", slug="fx", moysklad_id="fx", currency="RUBLE"),
        _product(sku="OLD", slug="old", moysklad_id="old", old_price=999),
    ):
        db = _session()
        db.add(product)
        with pytest.raises(HTTPException):
            db.commit()
        db.rollback()


def test_database_rejects_duplicate_provider_product_and_variant_ids():
    db = _session()
    first = _product()
    second = _product(
        sku="PRODUCT-2",
        slug="product-2",
        moysklad_id="provider-product-1",
    )
    db.add_all([first, second])
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()

    first = _product(moysklad_id="provider-product-a")
    second = _product(
        sku="PRODUCT-2",
        slug="product-2",
        moysklad_id="provider-product-b",
    )
    db.add_all([first, second])
    db.flush()
    db.add_all(
        [
            ProductVariant(
                product_id=first.id,
                size="M",
                color="",
                sku="VARIANT-1",
                moysklad_id="provider-variant-1",
                stock_qty=1,
                reserved_qty=0,
            ),
            ProductVariant(
                product_id=second.id,
                size="L",
                color="",
                sku="VARIANT-2",
                moysklad_id="provider-variant-1",
                stock_qty=1,
                reserved_qty=0,
            ),
        ]
    )
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_direct_sql_rejects_incoherent_product_and_sync_states():
    db = _session()
    with pytest.raises(IntegrityError):
        db.execute(
            Product.__table__.insert().values(
                sku="BAD",
                moysklad_id="",
                title="Bad",
                slug="bad",
                brand="FLASHIN",
                description="",
                price=0,
                old_price=None,
                currency="RUB",
                category="Clothing",
                gender="unisex",
                active=True,
            )
        )
        db.commit()
    db.rollback()

    with pytest.raises(IntegrityError):
        db.execute(
            MoySkladSyncLog.__table__.insert().values(
                sync_type="manual",
                status="success",
                products_seen=0,
                products_upserted=0,
                variants_upserted=0,
                error="",
                finished_at=None,
            )
        )
        db.commit()
    db.rollback()


def test_only_one_running_moysklad_sync_is_allowed():
    db = _session()
    db.add(MoySkladSyncLog(sync_type="manual", status="started", error=""))
    db.commit()
    db.add(MoySkladSyncLog(sync_type="scheduled", status="started", error=""))

    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_price_conversion_uses_decimal_rounding():
    assert moysklad._price_from_moysklad(
        {"salePrices": [{"value": "1000.5"}]}
    ) == 10.01
    assert moysklad._price_from_moysklad(
        {"salePrices": [{"value": "not-a-number"}]}
    ) == 0


def test_sync_fetch_failure_does_not_partially_apply_catalog(monkeypatch):
    db = _session()
    calls = 0

    async def fake_fetch(limit: int, offset: int):
        nonlocal calls
        calls += 1
        if calls == 1:
            return {
                "rows": [
                    {
                        "id": "provider-1",
                        "article": "SKU-1",
                        "name": "First",
                        "salePrices": [{"value": 100000}],
                        "stock": 5,
                    }
                ]
            }
        raise TimeoutError("second page failed")

    monkeypatch.setattr(moysklad, "get_settings", lambda: _settings(moysklad_sync_limit=1))
    monkeypatch.setattr(moysklad, "fetch_assortment", fake_fetch)

    result = asyncio.run(moysklad.sync_assortment_to_catalog(db))

    assert result.status == "failed"
    assert "second page failed" in result.error
    assert db.query(Product).count() == 0
    assert db.query(ProductVariant).count() == 0


def test_provider_identity_allows_safe_sku_rename(monkeypatch):
    db = _session()
    product = _product(sku="OLD-SKU", slug="old-sku", moysklad_id="provider-1")
    db.add(product)
    db.flush()
    db.add(
        ProductVariant(
            product_id=product.id,
            size="OLD",
            color="",
            sku="OLD-SKU",
            moysklad_id="provider-1",
            stock_qty=2,
            reserved_qty=1,
        )
    )
    db.commit()

    async def fake_fetch(limit: int, offset: int):
        return {
            "rows": [
                {
                    "id": "provider-1",
                    "article": "NEW-SKU",
                    "name": "Renamed product",
                    "salePrices": [{"value": 125050}],
                    "stock": 7,
                    "size": "M",
                }
            ]
        }

    monkeypatch.setattr(moysklad, "get_settings", lambda: _settings())
    monkeypatch.setattr(moysklad, "fetch_assortment", fake_fetch)

    result = asyncio.run(moysklad.sync_assortment_to_catalog(db))

    assert result.status == "success"
    assert result.products_seen == 1
    assert db.query(Product).count() == 1
    assert db.query(ProductVariant).count() == 1
    db.refresh(product)
    variant = db.query(ProductVariant).one()
    assert product.sku == "NEW-SKU"
    assert product.title == "Renamed product"
    assert product.price == 1250.5
    assert variant.sku == "NEW-SKU"
    assert variant.size == "M"
    assert variant.stock_qty == 7
    assert variant.reserved_qty == 1


def test_active_sync_returns_conflict_before_fetch(monkeypatch):
    db = _session()
    db.add(MoySkladSyncLog(sync_type="manual", status="started", error=""))
    db.commit()

    async def should_not_fetch(*_args, **_kwargs):
        raise AssertionError("fetch must not run")

    monkeypatch.setattr(moysklad, "get_settings", lambda: _settings())
    monkeypatch.setattr(moysklad, "fetch_assortment", should_not_fetch)

    with pytest.raises(moysklad.MoySkladSyncInProgress):
        asyncio.run(moysklad.sync_assortment_to_catalog(db))


def test_repeated_provider_identity_aborts_snapshot(monkeypatch):
    calls = 0

    async def fake_fetch(limit: int, offset: int):
        nonlocal calls
        calls += 1
        return {
            "rows": [
                {
                    "id": "provider-1",
                    "article": f"SKU-{calls}",
                }
            ]
        }

    monkeypatch.setattr(moysklad, "fetch_assortment", fake_fetch)

    with pytest.raises(ValueError, match="duplicate item id"):
        asyncio.run(moysklad._fetch_assortment_snapshot(1))


def test_migration_repairs_before_enabling_catalog_constraints():
    source = (
        Path(__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / "0029_catalog_moysklad_integrity.py"
    ).read_text(encoding="utf-8")

    assert source.index("UPDATE products") < source.index("op.create_check_constraint")
    assert "uq_products_moysklad_id" in source
    assert "uq_moysklad_sync_logs_single_started" in source
    assert 'down_revision = "0028_notification_delivery_integrity"' in source


def test_service_has_no_per_page_catalog_commit():
    source = inspect.getsource(moysklad._fetch_assortment_snapshot)
    assert ".commit(" not in source
