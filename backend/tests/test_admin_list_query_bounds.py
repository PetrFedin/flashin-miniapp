from pathlib import Path

import pytest
from fastapi import Response
from fastapi.routing import APIRoute
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.api import admin_list_bounds
from backend.database import Base
from backend.main import app
from backend.models import Product, ProductImage, ProductVariant


ROOT = Path(__file__).resolve().parents[2]


def _factory():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autocommit=False, autoflush=False)


def _product(index: int) -> Product:
    product = Product(
        sku=f"SKU-{index}",
        title=f"Product {index}",
        slug=f"product-{index}",
        brand="FLASHIN",
        price=100 + index,
        currency="RUB",
        category="Clothing",
        gender="unisex",
        active=True,
    )
    product.images.append(
        ProductImage(url=f"https://cdn.example.com/{index}.jpg", sort_order=0)
    )
    product.variants.append(
        ProductVariant(
            sku=f"SKU-{index}-M",
            size="M",
            color="Black",
            stock_qty=index,
            reserved_qty=0,
        )
    )
    return product


def test_all_bounded_admin_routes_replace_legacy_routes_once():
    expected = {
        "/api/admin/products": admin_list_bounds.bounded_products,
        "/api/admin/orders": admin_list_bounds.bounded_orders,
        "/api/admin/audit-logs": admin_list_bounds.bounded_audit_logs,
        "/api/admin/customers": admin_list_bounds.bounded_customers,
        "/api/admin/moysklad/mapping-rules": admin_list_bounds.bounded_mapping_rules,
        "/api/admin/moysklad/conflicts": admin_list_bounds.bounded_moysklad_conflicts,
    }

    for path, endpoint in expected.items():
        matching = [
            route
            for route in app.routes
            if isinstance(route, APIRoute)
            and route.path == path
            and "GET" in route.methods
        ]
        assert len(matching) == 1
        assert matching[0].endpoint is endpoint


def test_route_replacement_is_idempotent():
    admin_list_bounds.install_admin_list_bounds()

    for path in (
        "/admin/products",
        "/admin/orders",
        "/admin/audit-logs",
        "/admin/customers",
        "/admin/moysklad/mapping-rules",
        "/admin/moysklad/conflicts",
    ):
        matching = [
            route
            for route in admin_list_bounds.admin_router.routes
            if isinstance(route, APIRoute)
            and route.path == path
            and "GET" in route.methods
        ]
        assert len(matching) == 1


def test_products_are_stably_bounded_and_expose_page_headers(monkeypatch):
    db = _factory()()
    db.add_all([_product(index) for index in range(1, 5)])
    db.commit()
    permissions = []
    monkeypatch.setattr(
        admin_list_bounds,
        "require_permission",
        lambda _db, _admin, permission: permissions.append(permission),
    )
    response = Response()

    first_page = admin_list_bounds.bounded_products(
        response,
        limit=2,
        offset=0,
        admin=object(),
        db=db,
    )

    assert [item.sku for item in first_page] == ["SKU-4", "SKU-3"]
    assert response.headers["x-page-limit"] == "2"
    assert response.headers["x-page-offset"] == "0"
    assert response.headers["x-has-more"] == "true"
    assert response.headers["cache-control"] == "no-store, max-age=0"
    assert permissions == ["products.read"]
    assert first_page[0].images[0].url.endswith("4.jpg")
    assert first_page[0].variants[0].sku == "SKU-4-M"

    response = Response()
    second_page = admin_list_bounds.bounded_products(
        response,
        limit=2,
        offset=2,
        admin=object(),
        db=db,
    )
    assert [item.sku for item in second_page] == ["SKU-2", "SKU-1"]
    assert response.headers["x-has-more"] == "false"


def test_bounded_helper_uses_limit_plus_one_without_count_query():
    source = (ROOT / "backend" / "api" / "admin_list_bounds.py").read_text(
        encoding="utf-8"
    )

    assert ".limit(limit + 1)" in source
    assert ".count()" not in source
    assert "rows[:limit], len(rows) > limit" in source
    assert "MAX_LIST_LIMIT = 500" in source
    assert "MAX_LIST_OFFSET = 10_000_000" in source


def test_product_and_order_collections_use_selectinload_not_joinedload():
    source = (ROOT / "backend" / "api" / "admin_list_bounds.py").read_text(
        encoding="utf-8"
    )

    assert "selectinload(Product.images)" in source
    assert "selectinload(Product.variants)" in source
    assert "selectinload(Order.items)" in source
    assert "joinedload" not in source


def test_every_query_has_stable_tie_breaking_order():
    source = (ROOT / "backend" / "api" / "admin_list_bounds.py").read_text(
        encoding="utf-8"
    )

    assert "Product.created_at.desc(), Product.id.desc()" in source
    assert "Order.created_at.desc(), Order.id.desc()" in source
    assert "AuditLog.created_at.desc(), AuditLog.id.desc()" in source
    assert "Customer.created_at.desc(), Customer.id.desc()" in source
    assert "MoySkladConflict.created_at.desc(), MoySkladConflict.id.desc()" in source


@pytest.mark.parametrize(
    ("value", "expected"),
    [([], ([], False)), ([1], ([1], False)), ([1, 2], ([1], True))],
)
def test_bounded_helper_reports_has_more(value, expected):
    assert admin_list_bounds._bounded(value, limit=1) == expected
