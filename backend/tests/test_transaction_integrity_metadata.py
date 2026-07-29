from backend import model_constraints  # noqa: F401
from backend.models import (
    Cart,
    Payment,
    PaymentEvent,
    ProductVariant,
)


def _constraint_names(table) -> set[str]:
    return {constraint.name for constraint in table.constraints if constraint.name}


def _index_names(table) -> set[str]:
    return {index.name for index in table.indexes if index.name}


def test_inventory_constraints_are_registered():
    names = _constraint_names(ProductVariant.__table__)
    assert "ck_product_variants_stock_nonnegative" in names
    assert "ck_product_variants_reserved_nonnegative" in names
    assert "ck_product_variants_reserved_within_stock" in names


def test_active_cart_is_unique_per_customer():
    assert "uq_carts_one_active_per_customer" in _index_names(Cart.__table__)


def test_provider_payment_id_is_unique():
    assert "uq_payments_provider_payment_id" in _index_names(Payment.__table__)


def test_payment_event_is_unique():
    assert "uq_payment_events_provider_event" in _index_names(PaymentEvent.__table__)
