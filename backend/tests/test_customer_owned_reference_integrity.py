import importlib

import pytest
from sqlalchemy import ForeignKeyConstraint, UniqueConstraint, inspect

from backend import model_constraints  # noqa: F401 - applies metadata constraints on import
from backend.checkout_models import CheckoutAttempt
from backend.models import (
    Cart,
    LoyaltyRedemptionHold,
    LoyaltyTransaction,
    Order,
    ReturnRequest,
    SupportTicket,
)
from backend.pilot_models import PilotOrderSlot


migration = importlib.import_module(
    "backend.alembic.versions.0036_customer_owned_reference_integrity"
)


def _named_constraint(table, name):
    matches = [constraint for constraint in table.constraints if constraint.name == name]
    assert len(matches) == 1, f"expected exactly one constraint named {name}"
    return matches[0]


def test_parent_customer_identity_pairs_are_unique_in_metadata():
    order_constraint = _named_constraint(Order.__table__, "uq_orders_id_customer_id")
    cart_constraint = _named_constraint(Cart.__table__, "uq_carts_id_customer_id")

    assert isinstance(order_constraint, UniqueConstraint)
    assert tuple(order_constraint.columns.keys()) == ("id", "customer_id")
    assert isinstance(cart_constraint, UniqueConstraint)
    assert tuple(cart_constraint.columns.keys()) == ("id", "customer_id")


@pytest.mark.parametrize(
    ("table", "name", "local_columns", "remote_columns", "ondelete"),
    [
        (
            ReturnRequest.__table__,
            "fk_return_requests_order_customer",
            ("order_id", "customer_id"),
            ("orders.id", "orders.customer_id"),
            None,
        ),
        (
            SupportTicket.__table__,
            "fk_support_tickets_order_customer",
            ("order_id", "customer_id"),
            ("orders.id", "orders.customer_id"),
            None,
        ),
        (
            LoyaltyTransaction.__table__,
            "fk_loyalty_transactions_order_customer",
            ("order_id", "customer_id"),
            ("orders.id", "orders.customer_id"),
            None,
        ),
        (
            LoyaltyRedemptionHold.__table__,
            "fk_loyalty_redemption_holds_order_customer",
            ("order_id", "customer_id"),
            ("orders.id", "orders.customer_id"),
            None,
        ),
        (
            LoyaltyRedemptionHold.__table__,
            "fk_loyalty_redemption_holds_cart_customer",
            ("cart_id", "customer_id"),
            ("carts.id", "carts.customer_id"),
            None,
        ),
        (
            CheckoutAttempt.__table__,
            "fk_checkout_attempts_order_customer",
            ("order_id", "customer_id"),
            ("orders.id", "orders.customer_id"),
            None,
        ),
        (
            CheckoutAttempt.__table__,
            "fk_checkout_attempts_cart_customer",
            ("cart_id", "customer_id"),
            ("carts.id", "carts.customer_id"),
            None,
        ),
        (
            PilotOrderSlot.__table__,
            "fk_pilot_order_slots_order_customer",
            ("order_id", "customer_id"),
            ("orders.id", "orders.customer_id"),
            "CASCADE",
        ),
    ],
)
def test_customer_owned_reference_constraints_match_production_metadata(
    table,
    name,
    local_columns,
    remote_columns,
    ondelete,
):
    constraint = _named_constraint(table, name)

    assert isinstance(constraint, ForeignKeyConstraint)
    assert tuple(constraint.column_keys) == local_columns
    assert tuple(element.target_fullname for element in constraint.elements) == remote_columns
    assert constraint.ondelete == ondelete


def test_support_ticket_privacy_anonymization_remains_schema_valid():
    constraint = _named_constraint(
        SupportTicket.__table__,
        "fk_support_tickets_order_customer",
    )

    assert SupportTicket.__table__.c.customer_id.nullable is True
    assert constraint.match is None


def test_existing_order_return_relationship_stays_unambiguous():
    relationship = inspect(Order).relationships["returns"]

    assert relationship.mapper.class_ is ReturnRequest


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
    bind = _FakeBind([0] * len(migration.OWNERSHIP_CHECKS))

    migration._assert_customer_owned_references_are_clean(bind)

    assert len(bind.statements) == len(migration.OWNERSHIP_CHECKS)


def test_migration_preflight_fails_closed_without_rewriting_mismatch():
    counts = [0] * len(migration.OWNERSHIP_CHECKS)
    support_index = next(
        index
        for index, (label, _) in enumerate(migration.OWNERSHIP_CHECKS)
        if label == "support_tickets.order_id/customer_id"
    )
    counts[support_index] = 2
    bind = _FakeBind(counts)

    with pytest.raises(RuntimeError) as exc_info:
        migration._assert_customer_owned_references_are_clean(bind)

    message = str(exc_info.value)
    assert "migration will not rewrite or delete existing records" in message
    assert "support_tickets.order_id/customer_id=2" in message
    assert len(bind.statements) == len(migration.OWNERSHIP_CHECKS)
