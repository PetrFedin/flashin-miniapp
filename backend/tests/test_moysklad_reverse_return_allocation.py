import json
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.database import Base
from backend.models import Customer, Order, OrderItem, Product, ProductVariant, ReturnRequest
from backend.provider_models import ProviderCommand
from backend.reverse_logistics_models import ReturnLogisticsCase, ReturnLogisticsEvent, ReturnLogisticsItem
from backend.services import moysklad_reverse_return as reverse_moysklad
from backend.services.moysklad_outbound import MoySkladReviewRequired
from backend.services.provider_commands import enqueue_provider_command


def _db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _rounding_order(db):
    customer = Customer(telegram_id="rounding-allocation")
    product = Product(sku="ROUND-101", slug="round-101", title="Rounding", price=0.34)
    db.add_all([customer, product])
    db.flush()
    variant = ProductVariant(
        product_id=product.id,
        size="M",
        sku="ROUND-101-M",
        stock_qty=10,
        reserved_qty=0,
        moysklad_id="ms-round-101",
    )
    db.add(variant)
    db.flush()
    order = Order(
        customer_id=customer.id,
        status="completed",
        payment_status="paid",
        total_amount=1.01,
        discount_amount=0.01,
        loyalty_discount_amount=0,
        delivery_price=0,
        currency="RUB",
    )
    db.add(order)
    db.flush()
    item = OrderItem(
        order_id=order.id,
        product_id=product.id,
        variant_id=variant.id,
        title=product.title,
        size="M",
        quantity=3,
        price=0.34,
    )
    db.add(item)
    db.commit()
    return order, item, variant


def _complete_one_unit_case(db, order, item, variant, sequence: int):
    ret = ReturnRequest(
        order_id=order.id,
        customer_id=order.customer_id,
        reason=f"partial {sequence}",
        status="approved",
        refund_amount=0.34,
    )
    db.add(ret)
    db.flush()
    case = ReturnLogisticsCase(
        return_request_id=ret.id,
        order_id=order.id,
        customer_id=order.customer_id,
        status="inspected",
    )
    db.add(case)
    db.flush()
    physical = ReturnLogisticsItem(
        case_id=case.id,
        order_item_id=item.id,
        variant_id=variant.id,
        ordered_qty=3,
        authorized_qty=1,
        received_qty=1,
        inspected_qty=1,
        resalable_qty=1,
        damaged_qty=0,
        quarantine_qty=0,
    )
    db.add(physical)
    db.flush()
    db.add(
        ReturnLogisticsEvent(
            case_id=case.id,
            item_id=physical.id,
            event_type="inspected",
            disposition="resalable",
            quantity=1,
            idempotency_key=f"rounding-inspect-{sequence}",
            payload_hash=str(sequence) * 64,
            reason="rounding allocation regression",
        )
    )
    db.flush()
    payload = reverse_moysklad._build_physical_return_allocation(db, case.id)
    enqueue_provider_command(
        db,
        provider="moysklad",
        command_type="moysklad.physical_sales_return.create",
        idempotency_key=f"physical-return:{case.id}:sales_return:v1",
        aggregate_type="return_logistics_case",
        aggregate_id=case.id,
        payload=payload,
    )
    db.commit()
    return int(case.id), payload


def test_sibling_partial_returns_allocate_exact_original_line_cents_without_rounding_drift():
    db = _db()
    order, item, variant = _rounding_order(db)

    case_ids = []
    allocations = []
    for sequence in range(1, 4):
        case_id, payload = _complete_one_unit_case(db, order, item, variant, sequence)
        case_ids.append(case_id)
        allocations.append(int(payload["lines"][0]["net_total_cents"]))

    assert allocations == [34, 34, 33]
    assert sum(allocations) == 101

    # Later sibling returns may consume the remainder but must never rewrite
    # the immutable amount already bound to an earlier ProviderCommand.
    persisted = []
    for case_id in case_ids:
        payload = reverse_moysklad._persisted_allocation_payload(
            db,
            case_id=case_id,
            order_id=order.id,
        )
        persisted.append(int(payload["lines"][0]["net_total_cents"]))
    assert persisted == allocations


def test_tampered_physical_return_money_allocation_fails_closed():
    db = _db()
    order, item, variant = _rounding_order(db)
    case_id, _payload = _complete_one_unit_case(db, order, item, variant, 1)

    command = db.query(ProviderCommand).filter(
        ProviderCommand.provider == "moysklad",
        ProviderCommand.idempotency_key == f"physical-return:{case_id}:sales_return:v1",
    ).one()
    payload = json.loads(command.payload_json)
    payload["lines"][0]["net_total_cents"] += 1
    command.payload_json = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    db.commit()

    with pytest.raises(MoySkladReviewRequired, match="Immutable physical return monetary allocation"):
        reverse_moysklad._persisted_allocation_payload(
            db,
            case_id=case_id,
            order_id=order.id,
        )


def test_allocation_failure_persists_owned_fail_closed_provider_evidence(monkeypatch):
    db = _db()
    order, item, variant = _rounding_order(db)
    case_id, _payload = _complete_one_unit_case(db, order, item, variant, 1)

    # Remove only the synthetic successful command so enqueue can exercise its
    # allocation-failure path against the already durable physical case.
    db.query(ProviderCommand).filter(
        ProviderCommand.provider == "moysklad",
        ProviderCommand.idempotency_key == f"physical-return:{case_id}:sales_return:v1",
    ).delete(synchronize_session=False)
    db.commit()

    monkeypatch.setattr(
        reverse_moysklad,
        "get_settings",
        lambda: SimpleNamespace(moysklad_mode="live", moysklad_order_export_enabled=True),
    )

    def fail_allocation(*_args, **_kwargs):
        raise MoySkladReviewRequired("forced allocation failure")

    monkeypatch.setattr(
        reverse_moysklad,
        "_build_physical_return_allocation",
        fail_allocation,
    )

    command = reverse_moysklad.enqueue_moysklad_physical_sales_return(db, case_id)
    db.flush()
    payload = json.loads(command.payload_json)

    assert payload["case_id"] == case_id
    assert payload["order_id"] == order.id
    assert payload["allocation_error"] == "forced allocation failure"

    with pytest.raises(MoySkladReviewRequired, match="forced allocation failure"):
        reverse_moysklad._persisted_allocation_payload(
            db,
            case_id=case_id,
            order_id=order.id,
        )
