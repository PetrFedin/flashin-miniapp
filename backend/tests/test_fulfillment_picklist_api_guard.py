import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend import model_constraints  # noqa: F401
from backend.api import fulfillment as fulfillment_api
from backend.database import Base
from backend.models import Customer, FulfillmentTaskItem, Order, OrderItem
from backend.services.fulfillment import ensure_fulfillment_task, update_fulfillment_status


def _session():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def test_picklist_item_cannot_be_edited_after_task_is_packed(monkeypatch):
    db = _session()
    customer = Customer(telegram_id="picklist-api-user")
    db.add(customer)
    db.flush()
    order = Order(
        customer_id=customer.id,
        status="paid",
        payment_status="paid",
        total_amount=100.0,
        currency="RUB",
    )
    db.add(order)
    db.flush()
    order_item = OrderItem(
        order_id=order.id,
        product_id=1,
        variant_id=1,
        title="Item",
        quantity=1,
        price=100.0,
    )
    db.add(order_item)
    db.commit()
    order = db.query(Order).filter(Order.id == order.id).one()

    task = ensure_fulfillment_task(db, order)
    db.commit()
    update_fulfillment_status(db, task, "picking")
    db.flush()
    task_item = db.query(FulfillmentTaskItem).filter(FulfillmentTaskItem.task_id == task.id).one()
    task_item.status = "picked"
    task_item.picked_qty = 1
    db.commit()
    update_fulfillment_status(db, task, "packed")
    db.commit()

    monkeypatch.setattr(fulfillment_api, "require_permission", lambda *args, **kwargs: None)
    with pytest.raises(HTTPException) as caught:
        fulfillment_api.update_task_item(
            task_item.id,
            picked_qty=0,
            status="to_pick",
            issue="",
            admin=object(),
            db=db,
        )

    assert caught.value.status_code == 409
    assert "only while fulfillment is picking or blocked" in str(caught.value.detail)
    db.refresh(task_item)
    assert task_item.status == "picked"
    assert task_item.picked_qty == 1
