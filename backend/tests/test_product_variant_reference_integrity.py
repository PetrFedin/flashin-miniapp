import importlib

import pytest
from sqlalchemy import ForeignKeyConstraint, UniqueConstraint, inspect

from backend import model_constraints  # noqa: F401 - applies metadata constraints on import
from backend.models import CartItem, OrderItem, Product, ProductVariant


migration = importlib.import_module(
    "backend.alembic.versions.0037_product_variant_reference_integrity"
)


def _named_constraint(table, name):
    matches = [constraint for constraint in table.constraints if constraint.name == name]
    assert len(matches) == 1, f"expected exactly one constraint named {name}"
    return matches[0]


def test_variant_identity_pair_is_unique_in_metadata():
    constraint = _named_constraint(
        ProductVariant.__table__,
        "uq_product_variants_id_product_id",
    )

    assert isinstance(constraint, UniqueConstraint)
    assert tuple(constraint.columns.keys()) == ("id", "product_id")


@pytest.mark.parametrize(
    ("table", "name"),
    [
        (CartItem.__table__, "fk_cart_items_variant_product"),
        (OrderItem.__table__, "fk_order_items_variant_product"),
    ],
)
def test_product_variant_pair_constraints_match_production_metadata(table, name):
    constraint = _named_constraint(table, name)

    assert isinstance(constraint, ForeignKeyConstraint)
    assert tuple(constraint.column_keys) == ("variant_id", "product_id")
    assert tuple(element.target_fullname for element in constraint.elements) == (
        "product_variants.id",
        "product_variants.product_id",
    )


def test_existing_cart_relationships_remain_unambiguous():
    relationships = inspect(CartItem).relationships

    assert relationships["product"].mapper.class_ is Product
    assert relationships["variant"].mapper.class_ is ProductVariant


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


def test_migration_preflight_accepts_clean_legacy_data():
    bind = _FakeBind([0] * len(migration.REFERENCE_CHECKS))

    migration._assert_product_variant_references_are_clean(bind)

    assert len(bind.statements) == len(migration.REFERENCE_CHECKS)


@pytest.mark.parametrize(
    ("label", "count"),
    [
        ("cart_items.variant_id/product_id", 2),
        ("order_items.variant_id/product_id", 3),
    ],
)
def test_migration_preflight_fails_closed_without_rewriting_mismatch(label, count):
    counts = [0] * len(migration.REFERENCE_CHECKS)
    index = next(
        index
        for index, (check_label, _) in enumerate(migration.REFERENCE_CHECKS)
        if check_label == label
    )
    counts[index] = count
    bind = _FakeBind(counts)

    with pytest.raises(RuntimeError) as exc_info:
        migration._assert_product_variant_references_are_clean(bind)

    message = str(exc_info.value)
    assert "migration will not rewrite or delete existing cart/order records" in message
    assert f"{label}={count}" in message
    assert len(bind.statements) == len(migration.REFERENCE_CHECKS)
