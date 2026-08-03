from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.database import Base
from backend.models import Customer, Order
from backend.pilot_models import PilotOrderSlot, PilotRuntimeState
from scripts.check_pilot_runtime_integrity import run_audit


def _database():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return engine, sessionmaker(bind=engine)()


def test_closed_singleton_without_slots_passes_integrity_audit():
    engine, db = _database()
    db.add(PilotRuntimeState(id=1, status="closed", max_orders=20, accepted_orders=0))
    db.commit()

    with engine.connect() as connection:
        results = run_audit(connection)

    assert all(count == 0 for count in results.values()), results


def test_counter_drift_and_order_customer_mismatch_are_detected():
    engine, db = _database()
    first = Customer(telegram_id="1001")
    second = Customer(telegram_id="1002")
    db.add_all([first, second])
    db.flush()
    order = Order(customer_id=first.id, total_amount=100, currency="RUB")
    db.add(order)
    db.flush()
    db.add(
        PilotRuntimeState(
            id=1,
            run_id="pilot-run",
            status="active",
            admission_sha256="a" * 64,
            release_sha256="b" * 64,
            max_orders=20,
            accepted_orders=2,
        )
    )
    db.add(
        PilotOrderSlot(
            run_id="pilot-run",
            sequence=1,
            order_id=order.id,
            customer_id=second.id,
            admission_sha256="a" * 64,
        )
    )
    db.commit()

    with engine.connect() as connection:
        results = run_audit(connection)

    assert results["pilot_runtime_slot_counter_mismatch"] == 1
    assert results["orphan_or_mismatched_pilot_order_slots"] == 1
