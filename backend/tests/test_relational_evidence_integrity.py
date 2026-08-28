from pathlib import Path

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from backend import model_constraints as _model_constraints  # noqa: F401
from backend.database import Base
from backend.models import (
    Customer,
    FulfillmentTask,
    FulfillmentTaskItem,
    Order,
    OrderItem,
    Payment,
    PaymentReconciliation,
    Product,
    ProductVariant,
)


MIGRATION = (
    Path(__file__).parents[1]
    / "alembic"
    / "versions"
    / "0038_relational_evidence_integrity.py"
)


def _database():
    engine = create_engine("sqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _seed_relations(db):
    customer = Customer(telegram_id="relational-integrity-customer")
    product = Product(
        sku="relational-integrity-product",
        title="Integrity product",
        slug="relational-integrity-product",
        price=100,
    )
    db.add_all([customer, product])
    db.flush()

    variant = ProductVariant(
        product_id=product.id,
        size="M",
        color="black",
        sku="relational-integrity-variant",
        stock_qty=10,
        reserved_qty=0,
    )
    db.add(variant)
    db.flush()

    order_a = Order(customer_id=customer.id, total_amount=100, currency="RUB")
    order_b = Order(customer_id=customer.id, total_amount=100, currency="RUB")
    db.add_all([order_a, order_b])
    db.flush()

    item_a = OrderItem(
        order_id=order_a.id,
        product_id=product.id,
        variant_id=variant.id,
        title=product.title,
        size=variant.size,
        quantity=1,
        price=100,
    )
    item_b = OrderItem(
        order_id=order_b.id,
        product_id=product.id,
        variant_id=variant.id,
        title=product.title,
        size=variant.size,
        quantity=1,
        price=100,
    )
    task_a = FulfillmentTask(order_id=order_a.id, status="new")
    payment_a = Payment(
        order_id=order_a.id,
        provider="yookassa",
        provider_payment_id="relational-integrity-payment",
        status="paid",
        amount=100,
    )
    db.add_all([item_a, item_b, task_a, payment_a])
    db.commit()
    return order_a, order_b, item_a, item_b, task_a, payment_a


def _constraint_names(table) -> set[str]:
    return {constraint.name for constraint in table.constraints if constraint.name}


def test_metadata_contains_payment_order_identity_pair():
    assert "uq_payments_id_order_id" in _constraint_names(Payment.__table__)
    assert "fk_payment_reconciliations_payment_order" in _constraint_names(
        PaymentReconciliation.__table__
    )


def test_create_all_rejects_payment_reconciliation_linked_to_another_order():
    db = _database()
    _order_a, order_b, _item_a, _item_b, _task_a, payment_a = _seed_relations(db)
    db.add(
        PaymentReconciliation(
            payment_id=payment_a.id,
            order_id=order_b.id,
            provider_payment_id=payment_a.provider_payment_id,
            local_status="paid",
            provider_status="paid",
            amount_local=100,
            amount_provider=100,
            status="matched",
        )
    )

    with pytest.raises(IntegrityError):
        db.commit()


def test_reconciliation_can_still_record_order_evidence_without_local_payment():
    db = _database()
    _order_a, order_b, _item_a, _item_b, _task_a, _payment_a = _seed_relations(db)
    db.add(
        PaymentReconciliation(
            payment_id=None,
            order_id=order_b.id,
            provider_payment_id="provider-orphan-evidence",
            local_status="",
            provider_status="succeeded",
            amount_local=0,
            amount_provider=100,
            status="mismatch",
        )
    )

    db.commit()
    assert db.query(PaymentReconciliation).count() == 1


def test_create_all_allows_same_order_fulfillment_task_item():
    db = _database()
    _order_a, _order_b, item_a, _item_b, task_a, _payment_a = _seed_relations(db)
    db.add(
        FulfillmentTaskItem(
            task_id=task_a.id,
            order_item_id=item_a.id,
            status="to_pick",
            picked_qty=0,
        )
    )

    db.commit()
    assert db.query(FulfillmentTaskItem).count() == 1


def test_create_all_rejects_cross_order_fulfillment_task_item():
    db = _database()
    _order_a, _order_b, _item_a, item_b, task_a, _payment_a = _seed_relations(db)
    db.add(
        FulfillmentTaskItem(
            task_id=task_a.id,
            order_item_id=item_b.id,
            status="to_pick",
            picked_qty=0,
        )
    )

    with pytest.raises(IntegrityError):
        db.commit()


def test_migration_is_fail_closed_and_never_rewrites_business_rows():
    source = MIGRATION.read_text(encoding="utf-8")

    assert "0037_product_variant_reference_integrity" in source
    assert "payment reconciliations reference an order different" in source
    assert "fulfillment task items reference order items from another order" in source
    assert "fk_payment_reconciliations_payment_order" in source
    assert "trg_fulfillment_task_items_same_order" in source
    assert "CREATE OR REPLACE FUNCTION enforce_fulfillment_task_item_same_order" in source
    assert "UPDATE payment_reconciliations" not in source
    assert "DELETE FROM payment_reconciliations" not in source
    assert "UPDATE fulfillment_task_items" not in source
    assert "DELETE FROM fulfillment_task_items" not in source
