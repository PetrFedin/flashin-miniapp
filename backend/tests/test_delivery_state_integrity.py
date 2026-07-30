import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from backend import model_constraints  # noqa: F401
from backend.database import Base
from backend.models import Customer, DeliveryProvider, DeliveryShipment, Order
from backend.services.delivery_providers import (
    create_shipment,
    normalize_provider_code,
    update_tracking,
)


def _session():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _order(
    db,
    *,
    status="ready",
    payment_status="paid",
    delivery_status="ready",
    delivery_price=700.0,
):
    customer = Customer(telegram_id=str(100200300 + db.query(Customer).count()))
    db.add(customer)
    db.flush()
    order = Order(
        customer_id=customer.id,
        status=status,
        payment_status=payment_status,
        delivery_status=delivery_status,
        total_amount=5000.0,
        delivery_price=delivery_price,
        currency="RUB",
        delivery_type="courier",
    )
    db.add(order)
    db.commit()
    return order


def test_provider_code_is_normalized_and_strict():
    assert normalize_provider_code(" CDEK ") == "cdek"
    for invalid in ("", "bad code", "/courier", "a" * 65):
        with pytest.raises(HTTPException) as caught:
            normalize_provider_code(invalid)
        assert caught.value.status_code == 400


def test_shipment_uses_charged_order_price_and_is_idempotent():
    db = _session()
    order = _order(db, delivery_price=735.25)

    first = create_shipment(db, order.id, "courier")
    db.commit()
    second = create_shipment(db, order.id, "courier")
    db.commit()

    assert first.id == second.id
    assert first.price == 735.25
    assert first.status == "created"
    assert db.query(DeliveryShipment).count() == 1
    assert '"charged_delivery_price":"735.25"' in first.raw_payload


def test_shipment_requires_paid_ready_order():
    db = _session()
    unpaid = _order(
        db,
        status="created",
        payment_status="pending",
        delivery_status="not_started",
    )
    with pytest.raises(HTTPException) as unpaid_error:
        create_shipment(db, unpaid.id, "courier")
    assert unpaid_error.value.status_code == 409

    db = _session()
    assembling = _order(db, status="assembling", delivery_status="assembling")
    with pytest.raises(HTTPException) as not_ready:
        create_shipment(db, assembling.id, "courier")
    assert not_ready.value.status_code == 409


def test_unknown_or_inactive_provider_is_rejected():
    db = _session()
    order = _order(db)
    with pytest.raises(HTTPException) as unknown:
        create_shipment(db, order.id, "unknown-provider")
    assert unknown.value.status_code == 404

    db.add(
        DeliveryProvider(
            code="custom",
            name="Custom provider",
            active=False,
            config_json="{}",
        )
    )
    db.commit()
    with pytest.raises(HTTPException) as inactive:
        create_shipment(db, order.id, "custom")
    assert inactive.value.status_code == 409


def test_existing_shipment_cannot_be_silently_reassigned():
    db = _session()
    order = _order(db)
    create_shipment(db, order.id, "courier")
    db.commit()

    with pytest.raises(HTTPException) as caught:
        create_shipment(db, order.id, "cdek")
    assert caught.value.status_code == 409
    assert "another provider" in str(caught.value.detail)


def test_cancelled_shipment_can_be_reused_with_another_provider():
    db = _session()
    order = _order(db)
    shipment = create_shipment(db, order.id, "courier")
    db.commit()
    update_tracking(db, shipment.id, "", "cancelled")
    db.commit()

    replacement = create_shipment(db, order.id, "cdek")
    db.commit()

    assert replacement.id == shipment.id
    assert replacement.provider_code == "cdek"
    assert replacement.status == "created"
    assert replacement.tracking_number == ""
    assert db.query(DeliveryShipment).count() == 1


def test_shipping_and_delivery_update_order_atomically():
    db = _session()
    order = _order(db)
    shipment = create_shipment(db, order.id, "cdek")
    db.commit()

    update_tracking(db, shipment.id, "TRACK-001", "shipped")
    db.commit()
    assert shipment.status == "shipped"
    assert order.status == "shipped"
    assert order.delivery_status == "shipped"
    assert order.tracking_number == "TRACK-001"

    update_tracking(db, shipment.id, "TRACK-001", "delivered")
    db.commit()
    assert shipment.status == "delivered"
    assert order.status == "completed"
    assert order.delivery_status == "delivered"


def test_tracking_is_required_and_cannot_be_rewritten():
    db = _session()
    order = _order(db)
    shipment = create_shipment(db, order.id, "courier")
    db.commit()

    with pytest.raises(HTTPException) as missing:
        update_tracking(db, shipment.id, "", "shipped")
    assert missing.value.status_code == 400

    update_tracking(db, shipment.id, "TRACK-001", "shipped")
    db.commit()
    with pytest.raises(HTTPException) as rewritten:
        update_tracking(db, shipment.id, "TRACK-002", "delivery_failed")
    assert rewritten.value.status_code == 409


def test_invalid_status_jumps_and_terminal_rewrites_are_rejected():
    db = _session()
    order = _order(db)
    shipment = create_shipment(db, order.id, "courier")
    db.commit()

    with pytest.raises(HTTPException) as jump:
        update_tracking(db, shipment.id, "TRACK-001", "delivered")
    assert jump.value.status_code == 409

    update_tracking(db, shipment.id, "TRACK-001", "shipped")
    db.commit()
    update_tracking(db, shipment.id, "TRACK-001", "delivered")
    db.commit()
    with pytest.raises(HTTPException) as terminal:
        update_tracking(db, shipment.id, "TRACK-001", "returned")
    assert terminal.value.status_code == 409


def test_delivery_failure_can_retry_and_return_does_not_auto_refund():
    db = _session()
    order = _order(db)
    shipment = create_shipment(db, order.id, "courier")
    db.commit()
    update_tracking(db, shipment.id, "TRACK-001", "shipped")
    db.commit()
    update_tracking(db, shipment.id, "TRACK-001", "delivery_failed")
    db.commit()
    assert order.status == "shipped"
    assert order.delivery_status == "delivery_failed"

    update_tracking(db, shipment.id, "TRACK-001", "shipped")
    db.commit()
    update_tracking(db, shipment.id, "TRACK-001", "returned")
    db.commit()
    assert shipment.status == "returned"
    assert order.status == "shipped"
    assert order.delivery_status == "returned"
    assert order.payment_status == "paid"


def test_database_rejects_duplicate_or_invalid_shipments():
    db = _session()
    order = _order(db)
    db.add(
        DeliveryShipment(
            order_id=order.id,
            provider_code="courier",
            status="created",
            price=0,
        )
    )
    db.commit()

    db.add(
        DeliveryShipment(
            order_id=order.id,
            provider_code="cdek",
            status="created",
            price=0,
        )
    )
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()

    another_order = _order(db)
    db.add(
        DeliveryShipment(
            order_id=another_order.id,
            provider_code="courier",
            status="invented",
            price=-1,
        )
    )
    with pytest.raises(IntegrityError):
        db.commit()
