from importlib import import_module
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import sessionmaker

from backend.database import Base
from backend.models import (
    Customer,
    MoySkladConflict,
    Order,
    OrderItem,
    Product,
    ProductVariant,
)
from backend.services.moysklad import _sync_assortment_row
from backend.services.moysklad_outbound import MoySkladReviewRequired, _snapshot_order


migration_0043 = import_module(
    "backend.alembic.versions.0043_moysklad_variant_identity_authority"
)


def _db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return engine, sessionmaker(bind=engine)()


def _settings():
    return SimpleNamespace(
        moysklad_sale_price_type="",
        moysklad_default_currency="RUB",
        moysklad_size_attribute_names="Размер,Size",
        moysklad_color_attribute_names="Цвет,Color",
    )


def _variant_row(
    variant_id: str,
    parent_id: str | None,
    sku: str,
    *,
    size: str = "M",
    color: str = "Black",
):
    row = {
        "id": variant_id,
        "article": sku,
        "name": f"Variant {sku}",
        "meta": {"type": "variant"},
        "size": size,
        "color": color,
        "salePrices": [{"value": 100000}],
    }
    if parent_id is not None:
        row["product"] = {
            "meta": {
                "href": (
                    "https://api.moysklad.ru/api/remap/1.2/entity/product/"
                    f"{parent_id}"
                )
            }
        }
    return row


def _product_row(provider_id: str, sku: str):
    return {
        "id": provider_id,
        "article": sku,
        "name": f"Product {sku}",
        "meta": {"type": "product"},
        "salePrices": [{"value": 100000}],
    }


def _sync(db, row):
    result = _sync_assortment_row(
        db,
        row,
        settings=_settings(),
        sync_type="test",
        admin_id=None,
    )
    db.commit()
    return result


def test_two_provider_variants_share_one_authoritative_parent_product():
    _engine, db = _db()

    _sync(db, _variant_row("variant-1", "parent-1", "SKU-1", size="S"))
    _sync(db, _variant_row("variant-2", "parent-1", "SKU-2", size="M"))

    products = db.query(Product).all()
    variants = db.query(ProductVariant).order_by(ProductVariant.id.asc()).all()
    assert len(products) == 1
    assert products[0].moysklad_id == "parent-1"
    assert [(row.moysklad_id, row.product_id) for row in variants] == [
        ("variant-1", products[0].id),
        ("variant-2", products[0].id),
    ]


def test_repeated_variant_sync_is_idempotent():
    _engine, db = _db()
    first = _variant_row("variant-1", "parent-1", "SKU-1")
    second = _variant_row("variant-2", "parent-1", "SKU-2", size="L")

    _sync(db, first)
    _sync(db, second)
    product_ids = [row.id for row in db.query(Product).all()]
    variant_ids = [row.id for row in db.query(ProductVariant).order_by(ProductVariant.id).all()]

    _sync(db, first)
    _sync(db, second)

    assert [row.id for row in db.query(Product).all()] == product_ids
    assert [row.id for row in db.query(ProductVariant).order_by(ProductVariant.id).all()] == variant_ids


def test_provider_variant_sku_rename_updates_same_local_identity():
    _engine, db = _db()
    _sync(db, _variant_row("variant-1", "parent-1", "OLD-SKU"))
    original = db.query(ProductVariant).filter(ProductVariant.moysklad_id == "variant-1").one()
    original_id = original.id

    _sync(db, _variant_row("variant-1", "parent-1", "NEW-SKU"))

    renamed = db.query(ProductVariant).filter(ProductVariant.moysklad_id == "variant-1").one()
    assert renamed.id == original_id
    assert renamed.sku == "NEW-SKU"
    assert db.query(ProductVariant).filter(ProductVariant.sku == "OLD-SKU").count() == 0


def test_standalone_product_sku_rename_updates_product_and_sellable_variant_identity():
    _engine, db = _db()
    _sync(db, _product_row("product-1", "PRODUCT-OLD"))
    product = db.query(Product).filter(Product.moysklad_id == "product-1").one()
    variant = db.query(ProductVariant).filter(ProductVariant.moysklad_id == "product-1").one()
    product_id = product.id
    variant_id = variant.id

    _sync(db, _product_row("product-1", "PRODUCT-NEW"))

    product = db.query(Product).filter(Product.moysklad_id == "product-1").one()
    variant = db.query(ProductVariant).filter(ProductVariant.moysklad_id == "product-1").one()
    assert product.id == product_id
    assert variant.id == variant_id
    assert product.sku == "PRODUCT-NEW"
    assert variant.sku == "PRODUCT-NEW"


def test_sku_presented_by_different_provider_variant_fails_closed():
    _engine, db = _db()
    _sync(db, _variant_row("variant-1", "parent-1", "COLLISION"))

    _sync(db, _variant_row("variant-2", "parent-1", "COLLISION"))

    assert db.query(ProductVariant).count() == 1
    conflict = db.query(MoySkladConflict).filter(
        MoySkladConflict.conflict_type == "variant_sku_collision",
        MoySkladConflict.status == "open",
    ).one()
    assert conflict.moysklad_id == "variant-2"


def test_same_provider_variant_under_another_parent_fails_closed_without_phantom_product():
    _engine, db = _db()
    _sync(db, _variant_row("variant-1", "parent-1", "SKU-1"))

    _sync(db, _variant_row("variant-1", "parent-2", "SKU-1"))

    assert db.query(Product).count() == 1
    assert db.query(Product).one().moysklad_id == "parent-1"
    conflict = db.query(MoySkladConflict).filter(
        MoySkladConflict.conflict_type == "parent_reassignment",
        MoySkladConflict.status == "open",
    ).one()
    assert conflict.moysklad_id == "variant-1"


def test_variant_missing_parent_identity_is_rejected_without_creating_catalog_rows():
    _engine, db = _db()

    _sync(db, _variant_row("variant-1", None, "SKU-1"))

    assert db.query(Product).count() == 0
    assert db.query(ProductVariant).count() == 0
    conflict = db.query(MoySkladConflict).filter(
        MoySkladConflict.conflict_type == "missing_parent_identity",
        MoySkladConflict.status == "open",
    ).one()
    assert conflict.moysklad_id == "variant-1"


def test_sku_fallback_only_adopts_previously_unmapped_local_variant_and_parent():
    _engine, db = _db()
    product = Product(
        sku="LOCAL-PARENT",
        moysklad_id="",
        title="Local",
        slug="local-parent",
        price=1000,
    )
    db.add(product)
    db.flush()
    variant = ProductVariant(
        product_id=product.id,
        size="M",
        color="Black",
        sku="ADOPT-ME",
        moysklad_id="",
        stock_qty=0,
        reserved_qty=0,
    )
    db.add(variant)
    db.commit()
    product_id = product.id
    variant_id = variant.id

    _sync(db, _variant_row("variant-1", "parent-1", "ADOPT-ME"))

    mapped_product = db.get(Product, product_id)
    mapped_variant = db.get(ProductVariant, variant_id)
    assert mapped_product.moysklad_id == "parent-1"
    assert mapped_variant.moysklad_id == "variant-1"
    assert db.query(Product).count() == 1
    assert db.query(ProductVariant).count() == 1


def test_mapped_variant_sku_rename_cannot_take_another_local_variant_sku():
    _engine, db = _db()
    _sync(db, _variant_row("variant-1", "parent-1", "OLD-SKU"))
    other_product = Product(
        sku="OTHER-PARENT",
        title="Other",
        slug="other-parent",
        price=1000,
    )
    db.add(other_product)
    db.flush()
    db.add(
        ProductVariant(
            product_id=other_product.id,
            size="L",
            sku="TAKEN-SKU",
            stock_qty=0,
            reserved_qty=0,
        )
    )
    db.commit()

    _sync(db, _variant_row("variant-1", "parent-1", "TAKEN-SKU"))

    mapped = db.query(ProductVariant).filter(ProductVariant.moysklad_id == "variant-1").one()
    assert mapped.sku == "OLD-SKU"
    assert db.query(MoySkladConflict).filter(
        MoySkladConflict.conflict_type == "variant_sku_collision",
        MoySkladConflict.status == "open",
    ).count() == 1


def test_identity_conflict_rolls_back_partial_parent_adoption_before_logging_evidence():
    _engine, db = _db()
    product = Product(
        sku="UNMAPPED-PARENT",
        moysklad_id="",
        title="Unmapped",
        slug="unmapped-parent",
        price=1000,
    )
    other_product = Product(
        sku="OTHER-PARENT",
        title="Other",
        slug="other-parent-savepoint",
        price=1000,
    )
    db.add_all([product, other_product])
    db.flush()
    mapped = ProductVariant(
        product_id=product.id,
        size="M",
        sku="OLD-SKU",
        moysklad_id="variant-1",
        stock_qty=0,
        reserved_qty=0,
    )
    occupied = ProductVariant(
        product_id=other_product.id,
        size="L",
        sku="TAKEN-SKU",
        moysklad_id="",
        stock_qty=0,
        reserved_qty=0,
    )
    db.add_all([mapped, occupied])
    db.commit()
    product_id = product.id

    _sync(db, _variant_row("variant-1", "parent-1", "TAKEN-SKU"))

    db.expire_all()
    assert db.get(Product, product_id).moysklad_id == ""
    assert db.query(ProductVariant).filter(ProductVariant.moysklad_id == "variant-1").one().sku == "OLD-SKU"
    assert db.query(MoySkladConflict).filter(
        MoySkladConflict.conflict_type == "variant_sku_collision",
        MoySkladConflict.status == "open",
    ).count() == 1


def test_outbound_order_mapping_requires_exact_variant_provider_id_not_parent_fallback():
    _engine, db = _db()
    customer = Customer(telegram_id="identity-outbound")
    product = Product(
        sku="PARENT",
        moysklad_id="parent-provider-id",
        title="Parent",
        slug="identity-outbound-parent",
        price=1000,
    )
    db.add_all([customer, product])
    db.flush()
    variant = ProductVariant(
        product_id=product.id,
        size="M",
        sku="VARIANT",
        moysklad_id="",
        stock_qty=1,
        reserved_qty=0,
    )
    db.add(variant)
    db.flush()
    order = Order(
        customer_id=customer.id,
        total_amount=1000,
        delivery_price=0,
        discount_amount=0,
        loyalty_discount_amount=0,
        currency="RUB",
    )
    db.add(order)
    db.flush()
    item = OrderItem(
        order_id=order.id,
        product_id=product.id,
        variant_id=variant.id,
        title="Parent",
        size="M",
        quantity=1,
        price=1000,
    )
    db.add(item)
    db.flush()

    with pytest.raises(MoySkladReviewRequired, match="exact MoySklad assortment id"):
        _snapshot_order(order, [(item, variant, product)])


def test_create_all_mirrors_nonempty_provider_identity_unique_indexes():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    inspector = inspect(engine)

    product_indexes = {row["name"]: row for row in inspector.get_indexes("products")}
    variant_indexes = {
        row["name"]: row for row in inspector.get_indexes("product_variants")
    }
    assert product_indexes["uq_products_moysklad_id_nonempty"]["unique"] == 1
    assert variant_indexes["uq_product_variants_moysklad_id_nonempty"]["unique"] == 1


def test_migration_rejects_duplicate_legacy_provider_identity_without_mutating_rows():
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE products (id INTEGER PRIMARY KEY, moysklad_id VARCHAR(255) NOT NULL DEFAULT '')"
        )
        connection.exec_driver_sql(
            "CREATE TABLE product_variants (id INTEGER PRIMARY KEY, moysklad_id VARCHAR(255) NOT NULL DEFAULT '')"
        )
        connection.exec_driver_sql(
            "INSERT INTO products (id, moysklad_id) VALUES (1, 'duplicate-product'), (2, 'duplicate-product')"
        )
        connection.exec_driver_sql(
            "INSERT INTO product_variants (id, moysklad_id) VALUES (1, ''), (2, '')"
        )
        before = connection.exec_driver_sql(
            "SELECT id, moysklad_id FROM products ORDER BY id"
        ).all()

        with pytest.raises(RuntimeError, match="0043 blocked: duplicate non-empty MoySklad product identity"):
            migration_0043._assert_unique_provider_id_data(connection)

        after = connection.exec_driver_sql(
            "SELECT id, moysklad_id FROM products ORDER BY id"
        ).all()
        assert after == before
