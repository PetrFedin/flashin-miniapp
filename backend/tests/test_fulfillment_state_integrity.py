import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend import model_constraints  # noqa: F401
from backend.database import Base
from backend.models import (
    Customer,
    FulfillmentTask,
    FulfillmentTaskItem,
    Order,
    OrderItem,
    SlaEvent,
)
from backend.services.fulfillment import ensure_fulfillment_task, update_fulfillment_status


def _session():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _paid_order(db, quantities=(2, 1)):
    customer = Customer(telegram_id="fulfillment-user")
    db.add(customer)
    db.flush()
    order = Order(
        customer_id=customer.id,
        status="paid",
        payment_status="paid",
        delivery_status="not_started",
        total_amount=300.0,
        currency="RUB",
    )
    db.add(order)
    db.flush()
    for index, quantity in enumerate(quantities, start=1):
        db.add(
            OrderItem(
                order_id=order.id,
                product_id=index,
                variant_id=index,
                title=f"Item {index}",
                size="M",
                quantity=quantity,
                price=100.0,
            )
        )
    db.commit()
    return db.query(Order).filter(Order.id == order.id).one()


def test_fulfillment_task_and_sla_are_idempotent():
    db = _session()
    order = _paid_order(db)

    first = ensure_fulfillment_task(db, order)
    db.commit()
    second = ensure_fulfillment_task(db, order)
    db.commit()

    assert first.id == second.id
    assert db.query(FulfillmentTask).count() == 1
    assert db.query(FulfillmentTaskItem).count() == 2
    assert db.query(SlaEvent).filter(SlaEvent.event_type == "paid_to_assembling").count() == 1


def test_empty_paid_order_cannot_create_fulfillment_task():
    db = _session()
    order = _paid_order(db, quantities=())

    with pytest.raises(ValueError, match="no items"):
        ensure_fulfillment_task(db, order)


def test_incomplete_picklist_cannot_be_packed():
    db = _session()
    order = _paid_order(db)
    task = ensure_fulfillment_task(db, order)
    db.commit()

    update_fulfillment_status(db, task, "picking")
    db.commit()

    with pytest.raises(ValueError, match="Picklist is incomplete"):
        update_fulfillment_status(db, task, "packed")

    assert task.status == "picking"
    assert order.status == "assembling"


def test_full_picklist_can_progress_to_ready_and_resolves_sla():
    db = _session()
    order = _paid_order(db)
    task = ensure_fulfillment_task(db, order)
    db.commit()

    update_fulfillment_status(db, task, "picking")
    db.flush()
    rows = (
        db.query(FulfillmentTaskItem, OrderItem)
        .join(OrderItem, FulfillmentTaskItem.order_item_id == OrderItem.id)
        .filter(FulfillmentTaskItem.task_id == task.id)
        .all()
    )
    for task_item, order_item in rows:
        task_item.status = "picked"
        task_item.picked_qty = order_item.quantity
    db.commit()

    update_fulfillment_status(db, task, "packed")
    db.commit()
    assert task.status == "packed"
    assert order.status == "assembling"

    update_fulfillment_status(db, task, "ready")
    db.commit()
    assert task.status == "ready"
    assert order.status == "ready"
    assert order.delivery_status == "ready"
    assembling_sla = (
        db.query(SlaEvent)
        .filter(SlaEvent.event_type == "assembling_to_ready")
        .one()
    )
    assert assembling_sla.status == "resolved"
    assert assembling_sla.resolved_at is not None


def test_issue_item_prevents_packing_even_with_full_quantity():
    db = _session()
    order = _paid_order(db, quantities=(1,))
    task = ensure_fulfillment_task(db, order)
    db.commit()
    update_fulfillment_status(db, task, "picking")
    db.flush()

    task_item = db.query(FulfillmentTaskItem).filter(FulfillmentTaskItem.task_id == task.id).one()
    task_item.status = "issue"
    task_item.picked_qty = 1
    task_item.issue = "Damaged item"
    db.commit()

    with pytest.raises(ValueError, match="Picklist is incomplete"):
        update_fulfillment_status(db, task, "packed")


def test_refund_requested_order_cannot_progress_in_fulfillment():
    db = _session()
    order = _paid_order(db)
    task = ensure_fulfillment_task(db, order)
    db.commit()
    order.status = "refund_requested"
    db.commit()

    with pytest.raises(ValueError, match="cannot be fulfilled"):
        update_fulfillment_status(db, task, "picking")
