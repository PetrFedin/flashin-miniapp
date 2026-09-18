import importlib

import pytest
from sqlalchemy import create_engine, inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from backend import model_constraints  # noqa: F401 - applies create_all constraints
from backend.database import Base
from backend.models import Product, ProductVariant


migration = importlib.import_module(
    "backend.alembic.versions.0043_moysklad_variant_identity_authority"
)


class _ScalarResult:
    def __init__(self, value):
        self.value = value

    def scalar_one(self):
        return self.value


class _FakeBind:
    def __init__(self, counts):
        self._counts = iter(counts)
        self.statements = []

    def execute(self, statement):
        self.statements.append(str(statement))
        return _ScalarResult(next(self._counts))


def test_identity_migration_accepts_clean_legacy_data():
    bind = _FakeBind([0] * len(migration.IDENTITY_CHECKS))

    migration._assert_moysklad_identity_clean(bind)

    assert len(bind.statements) == len(migration.IDENTITY_CHECKS)


@pytest.mark.parametrize(
    ("label", "count"),
    [
        ("products.moysklad_id", 2),
        ("product_variants.moysklad_id", 3),
    ],
)
def test_identity_migration_rejects_duplicate_provider_ids_without_rewriting(
    label,
    count,
):
    counts = [0] * len(migration.IDENTITY_CHECKS)
    index = next(
        index
        for index, (check_label, _query) in enumerate(migration.IDENTITY_CHECKS)
        if check_label == label
    )
    counts[index] = count
    bind = _FakeBind(counts)

    with pytest.raises(RuntimeError) as exc_info:
        migration._assert_moysklad_identity_clean(bind)

    message = str(exc_info.value)
    assert "will not merge, delete, or rewrite provider mappings" in message
    assert f"{label} duplicate_groups={count}" in message


def _db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return engine, sessionmaker(bind=engine)()


def test_create_all_has_same_partial_provider_identity_indexes():
    engine, db = _db()
    try:
        product_indexes = {row["name"]: row for row in inspect(engine).get_indexes("products")}
        variant_indexes = {
            row["name"]: row
            for row in inspect(engine).get_indexes("product_variants")
        }

        assert product_indexes["uq_products_moysklad_id_nonempty"]["unique"] == 1
        assert (
            variant_indexes["uq_product_variants_moysklad_id_nonempty"]["unique"]
            == 1
        )
    finally:
        db.close()
        engine.dispose()


def test_create_all_rejects_duplicate_nonempty_product_provider_identity():
    engine, db = _db()
    try:
        db.add_all(
            [
                Product(
                    sku="A",
                    moysklad_id="same-product-id",
                    title="A",
                    slug="a",
                    price=1,
                ),
                Product(
                    sku="B",
                    moysklad_id="same-product-id",
                    title="B",
                    slug="b",
                    price=1,
                ),
            ]
        )
        with pytest.raises(IntegrityError):
            db.flush()
    finally:
        db.rollback()
        db.close()
        engine.dispose()


def test_create_all_allows_empty_product_provider_identity_for_local_records():
    engine, db = _db()
    try:
        db.add_all(
            [
                Product(sku="LOCAL-A", title="A", slug="local-a", price=1),
                Product(sku="LOCAL-B", title="B", slug="local-b", price=1),
            ]
        )
        db.flush()
        assert db.query(Product).count() == 2
    finally:
        db.rollback()
        db.close()
        engine.dispose()


def test_create_all_rejects_duplicate_nonempty_variant_provider_identity():
    engine, db = _db()
    try:
        first = Product(sku="P-A", title="A", slug="p-a", price=1)
        second = Product(sku="P-B", title="B", slug="p-b", price=1)
        db.add_all([first, second])
        db.flush()
        db.add_all(
            [
                ProductVariant(
                    product_id=first.id,
                    size="M",
                    color="Black",
                    sku="P-A-M",
                    moysklad_id="same-variant-id",
                ),
                ProductVariant(
                    product_id=second.id,
                    size="L",
                    color="Black",
                    sku="P-B-L",
                    moysklad_id="same-variant-id",
                ),
            ]
        )
        with pytest.raises(IntegrityError):
            db.flush()
    finally:
        db.rollback()
        db.close()
        engine.dispose()
