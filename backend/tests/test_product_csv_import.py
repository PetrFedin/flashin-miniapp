from decimal import Decimal

import pytest
from fastapi import HTTPException
from fastapi.routing import APIRoute
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.database import Base
from backend.main import app
from backend.models import Product, ProductVariant
from backend.services import product_csv_import
from backend.services.product_csv_import import import_products_csv, parse_product_csv


def _factory():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autocommit=False, autoflush=False)


def _csv(*rows: str, header: str = "sku,title,price,size,color,stock_qty") -> bytes:
    return (header + "\r\n" + "\r\n".join(rows) + "\r\n").encode("utf-8")


def test_product_import_endpoint_is_registered_once_as_post():
    routes = [
        route
        for route in app.routes
        if isinstance(route, APIRoute)
        and route.path == "/api/import-export/admin/products/import-csv"
    ]

    assert len(routes) == 1
    assert routes[0].methods == {"POST"}


def test_parse_accepts_utf8_bom_and_normalizes_defaults():
    content = b"\xef\xbb\xbfsku,title,price\r\nSKU-1,Test product,100.555\r\n"

    rows = parse_product_csv(content)

    assert len(rows) == 1
    row = rows[0]
    assert row.sku == "SKU-1"
    assert row.price == Decimal("100.56")
    assert row.currency == "RUB"
    assert row.category == "Clothing"
    assert row.active is True
    assert row.size == "ONE SIZE"
    assert row.variant_sku == "SKU-1"
    assert row.stock_qty == 0


def test_import_creates_and_updates_product_and_variant_atomically():
    db = _factory()()

    first = import_products_csv(
        db,
        _csv("SKU-1,Test product,100.00,M,Black,5"),
    )
    db.commit()

    assert first.as_dict() == {
        "rows": 1,
        "products_created": 1,
        "products_updated": 0,
        "variants_created": 1,
        "variants_updated": 0,
    }
    product = db.query(Product).one()
    variant = db.query(ProductVariant).one()
    original_slug = product.slug
    assert product.sku == "SKU-1"
    assert product.price == pytest.approx(100.0)
    assert product.slug.startswith("csv-")
    assert variant.sku == "SKU-1"
    assert variant.size == "M"
    assert variant.color == "Black"
    assert variant.stock_qty == 5

    second = import_products_csv(
        db,
        _csv("SKU-1,Updated product,125.25,M,Black,8"),
    )
    db.commit()
    db.refresh(product)
    db.refresh(variant)

    assert second.products_created == 0
    assert second.products_updated == 1
    assert second.variants_created == 0
    assert second.variants_updated == 1
    assert product.title == "Updated product"
    assert product.price == pytest.approx(125.25)
    assert product.slug == original_slug
    assert variant.stock_qty == 8


def test_repeated_product_rows_create_distinct_variants():
    db = _factory()()
    content = _csv(
        "SKU-2,Multi size,200.00,S,Black,3",
        "SKU-2,Multi size,200.00,M,Black,4",
    )

    result = import_products_csv(db, content)
    db.commit()

    assert result.products_created == 1
    assert result.variants_created == 2
    product = db.query(Product).one()
    variants = db.query(ProductVariant).order_by(ProductVariant.size).all()
    assert product.sku == "SKU-2"
    assert {(item.size, item.stock_qty) for item in variants} == {("S", 3), ("M", 4)}
    assert len({item.sku for item in variants}) == 2


@pytest.mark.parametrize(
    ("content", "message"),
    [
        (b"title,price\nProduct,10\n", "Missing CSV columns: sku"),
        (b"sku,title,price,unknown\nA,Product,10,x\n", "Unsupported CSV columns: unknown"),
        (b"sku,SKU,title,price\nA,A,Product,10\n", "duplicate columns"),
        (b"sku,title,price\nA,Product,NaN\n", "price must be between"),
        (b"sku,title,price,active\nA,Product,10,sometimes\n", "active must be true or false"),
        (b"sku,title,price,stock_qty\nA,Product,10,-1\n", "stock_qty must be between"),
        (b"sku,title,price\n\n", "contains no product rows"),
    ],
)
def test_parse_rejects_malformed_or_unsafe_csv(content, message):
    with pytest.raises(HTTPException) as exc_info:
        parse_product_csv(content)

    assert exc_info.value.status_code == 400
    assert message in str(exc_info.value.detail)


def test_parse_rejects_non_utf8_and_bounded_bytes(monkeypatch):
    with pytest.raises(HTTPException) as encoding_error:
        parse_product_csv(b"sku,title,price\nA,\xff,10\n")
    assert "UTF-8" in encoding_error.value.detail

    monkeypatch.setattr(product_csv_import, "MAX_PRODUCT_CSV_BYTES", 10)
    with pytest.raises(HTTPException) as size_error:
        parse_product_csv(b"sku,title,price\nA,Product,10\n")
    assert size_error.value.status_code == 413
    assert "exceeds" in size_error.value.detail


def test_parse_rejects_row_limit_before_database_work(monkeypatch):
    monkeypatch.setattr(product_csv_import, "MAX_PRODUCT_CSV_ROWS", 1)

    with pytest.raises(HTTPException) as exc_info:
        parse_product_csv(_csv("A,One,10,M,,1", "B,Two,20,M,,1"))

    assert exc_info.value.status_code == 413
    assert "more than 1" in exc_info.value.detail


def test_conflicting_product_facts_and_duplicate_variants_are_rejected():
    with pytest.raises(HTTPException) as facts_error:
        parse_product_csv(
            _csv(
                "SKU-3,First,10,S,Black,1",
                "SKU-3,Second,10,M,Black,1",
            )
        )
    assert "conflicting product fields" in facts_error.value.detail

    with pytest.raises(HTTPException) as duplicate_error:
        parse_product_csv(
            _csv(
                "SKU-3,First,10,S,Black,1",
                "SKU-3,First,10,S,Black,2",
            )
        )
    assert "size and color are duplicated" in duplicate_error.value.detail


def test_failed_import_rolls_back_product_changes_when_stock_is_reserved():
    db = _factory()()
    product = Product(
        sku="SKU-4",
        title="Original",
        slug="original-sku-4",
        brand="FLASHIN",
        price=100,
        currency="RUB",
        category="Clothing",
        gender="unisex",
        active=True,
    )
    variant = ProductVariant(
        product=product,
        sku="SKU-4",
        size="M",
        color="Black",
        stock_qty=5,
        reserved_qty=3,
    )
    db.add_all([product, variant])
    db.commit()

    with pytest.raises(HTTPException) as exc_info:
        import_products_csv(
            db,
            _csv("SKU-4,Changed,150.00,M,Black,2"),
        )
    assert exc_info.value.status_code == 409
    assert "reservations" in exc_info.value.detail
    db.rollback()
    db.refresh(product)
    db.refresh(variant)

    assert product.title == "Original"
    assert product.price == pytest.approx(100.0)
    assert variant.stock_qty == 5
    assert variant.reserved_qty == 3


def test_variant_identity_cannot_be_moved_between_products():
    db = _factory()()
    first = Product(
        sku="P-1",
        title="First",
        slug="p-1",
        brand="FLASHIN",
        price=100,
        currency="RUB",
        category="Clothing",
        gender="unisex",
        active=True,
    )
    db.add(
        ProductVariant(
            product=first,
            sku="SHARED-VARIANT",
            size="M",
            color="",
            stock_qty=1,
            reserved_qty=0,
        )
    )
    db.commit()
    content = _csv(
        "P-2,Second,100.00,M,,1,SHARED-VARIANT",
        header="sku,title,price,size,color,stock_qty,variant_sku",
    )

    with pytest.raises(HTTPException) as exc_info:
        import_products_csv(db, content)

    assert exc_info.value.status_code == 409
    assert "another product" in exc_info.value.detail
    db.rollback()
    assert db.query(Product).count() == 1
