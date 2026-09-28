from pathlib import Path
import sys

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from backend.database import Base  # noqa: E402
from backend.models import (  # noqa: E402
    Customer,
    InventoryMovement,
    Order,
    OrderItem,
    Payment,
    Product,
    ProductVariant,
    ReturnRequest,
)
from backend.pilot_models import PilotOrderSlot, PilotRuntimeState  # noqa: E402
from backend.services.inventory_movement_contract import (  # noqa: E402
    expected_inventory_delta,
    movement_transition_valid,
)
from backend.services.pilot_database_evidence import (  # noqa: E402
    validate_pilot_database_evidence,
)
from backend.services.pilot_inventory_evidence import (  # noqa: E402
    validate_order_inventory_evidence,
)
from pilot_control import SCENARIOS, new_state as _new_state, record_scenario  # noqa: E402
from pilot_control_audit import build_audit_entry, normalize_mutation  # noqa: E402

ADMISSION_SHA = "a" * 64
BINDING = {
    "manifest_sha256": ADMISSION_SHA,
    "created_at": "2026-08-05T12:00:00Z",
    "configuration_fingerprint": "b" * 64,
    "release": {
        "release_id": "pilot-release",
        "git_commit": "c" * 40,
        "sha256": "d" * 64,
    },
}
APPROVALS = {
    "business_owner": "Alice Business",
    "operations_owner": "Olga Operations",
    "technical_owner": "Tim Technical",
    "legal_owner": "Lena Legal",
    "support_owner": "Sam Support",
}


def _return_status(scenario: dict) -> str:
    if scenario["expected_order_status"] == "partially_refunded":
        return "approved_partial"
    return "approved"


def state_with_passed_scenarios():
    mutation = normalize_mutation(
        operation="init",
        operator_role="operations_owner",
        operator_name=APPROVALS["operations_owner"],
        reason="Initialize database-bound pilot",
        approvals=APPROVALS,
    )
    state = _new_state(
        BINDING,
        initial_audit=build_audit_entry(
            mutation,
            revision=1,
            parent_state_sha256=None,
        ),
    )
    for scenario in SCENARIOS:
        number = scenario["number"]
        amount = f"{1000 + number}.00"
        changes = {
            "result": "pass",
            "evidence": [f"db-evidence-{number}"],
            "order_id": str(number),
            "order_status": scenario["expected_order_status"],
        }
        if scenario.get("requires_payment"):
            changes.update(
                payment_id=f"provider-payment-{number}",
                payment_status="succeeded",
                expected_amount=amount,
                provider_amount=amount,
                currency="RUB",
                provider_currency="RUB",
            )
        if scenario.get("requires_refund"):
            changes.update(
                refund_id=f"provider-refund-{number}",
                refund_status=_return_status(scenario),
            )
        if scenario.get("requires_stock"):
            delta = scenario.get("expected_stock_delta", 1)
            changes.update(
                stock_before=10,
                stock_after=10 - delta,
                expected_stock_delta=delta,
            )
        if scenario.get("requires_webhook_idempotency"):
            changes.update(webhook_deliveries=2, domain_effects=1)
        record_scenario(state, number, **changes)
    return state


def populated_session():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    customer = Customer(id=1, telegram_id="1001")
    session.add(customer)
    runtime = PilotRuntimeState(
        id=1,
        run_id="run-v16",
        status="completed",
        admission_sha256=ADMISSION_SHA,
        release_sha256="d" * 64,
        pilot_state_created_at="2026-08-05T12:00:00Z",
        pilot_state_revision=1,
        pilot_state_sha256="e" * 64,
        max_orders=20,
        accepted_orders=20,
        allowed_telegram_ids='["1001"]',
    )
    session.add(runtime)
    for scenario in SCENARIOS:
        number = scenario["number"]
        amount = float(1000 + number)
        order = Order(
            id=number,
            customer_id=1,
            status=scenario["expected_order_status"],
            payment_status="paid",
            total_amount=amount,
            currency="RUB",
        )
        session.add(order)
        session.flush()
        if scenario.get("requires_stock"):
            delta = int(scenario.get("expected_stock_delta", 1))
            product = Product(
                sku=f"PILOT-PRODUCT-{number}",
                title=f"Pilot stock {number}",
                slug=f"pilot-stock-{number}",
                price=amount,
            )
            session.add(product)
            session.flush()
            variant = ProductVariant(
                product_id=product.id,
                sku=f"PILOT-STOCK-{number}",
                size="ONE",
                stock_qty=10 - delta,
                reserved_qty=0,
            )
            session.add(variant)
            session.flush()
            session.add(
                OrderItem(
                    order_id=number,
                    product_id=product.id,
                    variant_id=variant.id,
                    title=product.title,
                    size=variant.size,
                    quantity=1,
                    price=amount,
                )
            )
            session.add(
                InventoryMovement(
                    order_id=number,
                    variant_id=variant.id,
                    kind="reserve",
                    quantity=1,
                    stock_before=10,
                    stock_after=10,
                    reserved_before=0,
                    reserved_after=1,
                    source="checkout",
                )
            )
            session.add(
                InventoryMovement(
                    order_id=number,
                    variant_id=variant.id,
                    kind="release" if delta == 0 else "commit",
                    quantity=1,
                    stock_before=10,
                    stock_after=10 - delta,
                    reserved_before=1,
                    reserved_after=0,
                    source=(
                        "order_cancellation:pilot"
                        if delta == 0
                        else "payment_settlement"
                    ),
                )
            )
        session.add(
            PilotOrderSlot(
                run_id=runtime.run_id,
                sequence=number,
                order_id=number,
                customer_id=1,
                admission_sha256=ADMISSION_SHA,
            )
        )
        if scenario.get("requires_payment"):
            session.add(
                Payment(
                    order_id=number,
                    provider="yookassa",
                    provider_payment_id=f"provider-payment-{number}",
                    status="succeeded",
                    amount=amount,
                )
            )
        if scenario.get("requires_refund"):
            session.add(
                ReturnRequest(
                    order_id=number,
                    customer_id=1,
                    reason="pilot refund verification",
                    status=_return_status(scenario),
                    provider_refund_id=f"provider-refund-{number}",
                    refund_amount=(amount / 2 if _return_status(scenario) == "approved_partial" else amount),
                )
            )
    session.commit()
    return session, runtime


def _movement(
    *,
    kind: str,
    quantity: int,
    stock_before: int,
    stock_after: int,
    reserved_before: int,
    reserved_after: int,
) -> InventoryMovement:
    return InventoryMovement(
        order_id=1,
        variant_id=1,
        kind=kind,
        quantity=quantity,
        stock_before=stock_before,
        stock_after=stock_after,
        reserved_before=reserved_before,
        reserved_after=reserved_after,
        source="test",
    )


def _interleaved_same_sku_session():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    customer = Customer(telegram_id="interleaved-customer")
    product = Product(
        sku="INTERLEAVED",
        title="Interleaved inventory proof",
        slug="interleaved-inventory-proof",
        price=1000,
    )
    session.add_all([customer, product])
    session.flush()
    variant = ProductVariant(
        product_id=product.id,
        sku="INTERLEAVED-ONE",
        size="ONE",
        stock_qty=8,
        reserved_qty=0,
    )
    session.add(variant)
    session.flush()
    order_a = Order(
        customer_id=customer.id,
        status="paid",
        payment_status="paid",
        total_amount=1000,
        currency="RUB",
    )
    order_b = Order(
        customer_id=customer.id,
        status="paid",
        payment_status="paid",
        total_amount=1000,
        currency="RUB",
    )
    session.add_all([order_a, order_b])
    session.flush()
    session.add_all(
        [
            OrderItem(
                order_id=order_a.id,
                product_id=product.id,
                variant_id=variant.id,
                title=product.title,
                size=variant.size,
                quantity=1,
                price=1000,
            ),
            OrderItem(
                order_id=order_b.id,
                product_id=product.id,
                variant_id=variant.id,
                title=product.title,
                size=variant.size,
                quantity=1,
                price=1000,
            ),
        ]
    )
    # One physical SKU, two independent order chains. IDs intentionally encode
    # the real interleaving A.reserve -> B.reserve -> B.commit -> A.commit.
    session.add(
        InventoryMovement(
            order_id=order_a.id,
            variant_id=variant.id,
            kind="reserve",
            quantity=1,
            stock_before=10,
            stock_after=10,
            reserved_before=0,
            reserved_after=1,
            source="checkout:A",
        )
    )
    session.flush()
    session.add(
        InventoryMovement(
            order_id=order_b.id,
            variant_id=variant.id,
            kind="reserve",
            quantity=1,
            stock_before=10,
            stock_after=10,
            reserved_before=1,
            reserved_after=2,
            source="checkout:B",
        )
    )
    session.flush()
    session.add(
        InventoryMovement(
            order_id=order_b.id,
            variant_id=variant.id,
            kind="commit",
            quantity=1,
            stock_before=10,
            stock_after=9,
            reserved_before=2,
            reserved_after=1,
            source="payment_settlement:B",
        )
    )
    session.flush()
    session.add(
        InventoryMovement(
            order_id=order_a.id,
            variant_id=variant.id,
            kind="commit",
            quantity=1,
            stock_before=9,
            stock_after=8,
            reserved_before=1,
            reserved_after=0,
            source="payment_settlement:A",
        )
    )
    session.commit()
    return session, order_a


def test_exact_completed_twenty_order_database_evidence_is_accepted():
    session, runtime = populated_session()
    try:
        assert (
            validate_pilot_database_evidence(
                session,
                state_with_passed_scenarios(),
                runtime,
                final=True,
            )
            == []
        )
    finally:
        session.close()


def test_missing_or_wrong_slot_order_fails_closed():
    session, runtime = populated_session()
    state = state_with_passed_scenarios()
    state["scenarios"][0]["order_id"] = "2"
    try:
        errors = validate_pilot_database_evidence(session, state, runtime)
        assert any("does not match pilot slot" in error for error in errors)
        assert any("reuse a PostgreSQL order" in error for error in errors)
    finally:
        session.close()


def test_payment_refund_status_and_amount_are_read_from_postgresql():
    session, runtime = populated_session()
    state = state_with_passed_scenarios()
    first_payment = next(
        record for record in state["scenarios"] if record.get("payment_id")
    )
    first_payment["provider_amount"] = "1.00"
    refund_record = next(
        record for record in state["scenarios"] if record.get("refund_id")
    )
    refund_record["refund_status"] = "succeeded"
    try:
        errors = validate_pilot_database_evidence(session, state, runtime)
        assert any("provider_amount" in error for error in errors)
        assert any("refund_status" in error for error in errors)
    finally:
        session.close()


def test_unrelated_provider_identifier_or_admission_is_rejected():
    session, runtime = populated_session()
    state = state_with_passed_scenarios()
    first_payment = next(
        record for record in state["scenarios"] if record.get("payment_id")
    )
    first_payment["payment_id"] = "provider-payment-missing"
    runtime.admission_sha256 = "f" * 64
    try:
        errors = validate_pilot_database_evidence(session, state, runtime)
        assert any("resolved to 0 PostgreSQL rows" in error for error in errors)
        assert any("admission does not match" in error for error in errors)
    finally:
        session.close()


def test_final_go_rejects_active_or_incomplete_runtime():
    session, runtime = populated_session()
    runtime.status = "active"
    runtime.accepted_orders = 19
    try:
        errors = validate_pilot_database_evidence(
            session,
            state_with_passed_scenarios(),
            runtime,
            final=True,
        )
        assert any("status completed" in error for error in errors)
        assert any("exactly 20 accepted" in error for error in errors)
    finally:
        session.close()


def test_schema_five_is_explicitly_not_database_bound():
    session, runtime = populated_session()
    state = state_with_passed_scenarios()
    state["schema_version"] = 5
    try:
        assert validate_pilot_database_evidence(session, state, runtime) == [
            "pilot control state schema is not database-bound"
        ]
    finally:
        session.close()


def test_signed_stock_after_is_bound_to_order_movement_boundaries():
    session, runtime = populated_session()
    state = state_with_passed_scenarios()
    stock_record = next(
        record for record in state["scenarios"] if record.get("stock_before") is not None
    )
    stock_record["stock_after"] = 999
    try:
        errors = validate_pilot_database_evidence(session, state, runtime)
        assert any("signed stock_after" in error for error in errors)
    finally:
        session.close()


def test_missing_or_broken_inventory_chain_fails_closed():
    session, runtime = populated_session()
    state = state_with_passed_scenarios()
    stock_record = next(
        record for record in state["scenarios"] if record.get("stock_before") is not None
    )
    order_id = int(stock_record["order_id"])
    movement = (
        session.query(InventoryMovement)
        .filter(
            InventoryMovement.order_id == order_id,
            InventoryMovement.kind == "reserve",
        )
        .one()
    )
    movement.reserved_after = 9
    session.commit()
    try:
        errors = validate_pilot_database_evidence(session, state, runtime)
        assert any("reserve inventory transition is invalid" in error for error in errors)
    finally:
        session.close()


def test_expected_inventory_delta_is_semantic_and_order_local():
    reserve = _movement(
        kind="reserve",
        quantity=2,
        stock_before=10,
        stock_after=10,
        reserved_before=0,
        reserved_after=2,
    )
    commit = _movement(
        kind="commit",
        quantity=2,
        stock_before=10,
        stock_after=8,
        reserved_before=2,
        reserved_after=0,
    )
    physical_return = _movement(
        kind="return",
        quantity=1,
        stock_before=8,
        stock_after=9,
        reserved_before=0,
        reserved_after=0,
    )

    assert expected_inventory_delta([commit]) == 2
    assert expected_inventory_delta([physical_return]) == -1
    assert expected_inventory_delta([reserve, commit, physical_return]) == 1
    assert all(movement_transition_valid(row) for row in (reserve, commit, physical_return))


def test_interleaved_same_sku_order_proof_uses_only_its_own_movement_delta():
    session, order_a = _interleaved_same_sku_session()
    record = {
        "number": 101,
        "stock_before": 10,
        "stock_after": 8,
        "expected_stock_delta": 1,
    }
    try:
        assert validate_order_inventory_evidence(session, record, order_a) == []
    finally:
        session.close()


def test_interleaved_same_sku_order_cannot_sign_other_orders_commit():
    session, order_a = _interleaved_same_sku_session()
    record = {
        "number": 102,
        "stock_before": 10,
        "stock_after": 8,
        # Boundary difference is 2 because B committed between A's movements.
        # A owns only its own commit and must never be allowed to sign B's unit.
        "expected_stock_delta": 2,
    }
    try:
        errors = validate_order_inventory_evidence(session, record, order_a)
        assert any("scoped inventory movement delta" in error for error in errors)
        assert any("expected inventory contract delta" in error for error in errors)
    finally:
        session.close()
