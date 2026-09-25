import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.database import Base
from backend.models import Customer, Order, OrderItem, Product, ProductVariant, ReturnRequest
from backend.provider_models import ProviderCommand
from backend.reverse_logistics_models import ReturnLogisticsEvent, ReturnLogisticsItem
from backend.services import moysklad as moysklad_service
from backend.services import moysklad_reverse_return as reverse_moysklad
from backend.services.moysklad_outbound import MoySkladReviewRequired
from backend.services.provider_commands import enqueue_provider_command
from backend.services.reverse_logistics import (
    authorize_item,
    ensure_physical_case,
    inspect_item,
    mark_in_transit,
    receive_item,
    resolve_quarantine_item,
)


def _db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _settings():
    return SimpleNamespace(
        moysklad_mode="live",
        moysklad_order_export_enabled=True,
        moysklad_store_id="store-sellable",
        moysklad_damaged_store_id="store-damaged",
        moysklad_quarantine_store_id="store-quarantine",
        moysklad_base_url="https://api.example.invalid",
        moysklad_organization_id="org",
        moysklad_agent_id="agent",
    )


def _mixed_case(db):
    customer = Customer(telegram_id="disp-auth")
    product = Product(
        sku="DISP-P",
        slug="disp-p",
        title="Disposition",
        price=100,
        currency="RUB",
        active=True,
        moysklad_id="product-disp",
    )
    variant = ProductVariant(
        product=product,
        size="M",
        sku="DISP-V",
        stock_qty=10,
        reserved_qty=0,
        moysklad_id="variant-disp",
    )
    db.add_all([customer, product, variant])
    db.flush()
    order = Order(
        customer_id=customer.id,
        status="completed",
        payment_status="paid",
        delivery_status="delivered",
        total_amount=300,
        delivery_price=0,
        discount_amount=0,
        loyalty_discount_amount=0,
        currency="RUB",
        delivery_type="pickup",
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
        price=100,
    )
    ret = ReturnRequest(
        order_id=order.id,
        customer_id=customer.id,
        reason="mixed",
        status="approved",
        refund_amount=300,
    )
    db.add_all([item, ret])
    db.flush()
    db.add(
        ProviderCommand(
            provider="moysklad",
            command_type="moysklad.demand.create",
            idempotency_key=f"order:{order.id}:demand:v1",
            aggregate_type="order",
            aggregate_id=str(order.id),
            payload_json="{}",
            status="sent",
            external_id="demand-1",
        )
    )
    db.commit()

    case = ensure_physical_case(db, ret=ret, order=order)
    physical = db.query(ReturnLogisticsItem).filter(ReturnLogisticsItem.case_id == case.id).one()
    authorize_item(
        db,
        case_id=case.id,
        item_id=physical.id,
        quantity=3,
        idempotency_key="authorize-disp-1",
        actor_admin_id=None,
    )
    mark_in_transit(db, case_id=case.id)
    receive_item(
        db,
        case_id=case.id,
        item_id=physical.id,
        quantity=3,
        idempotency_key="receive-disp-1",
        actor_admin_id=None,
    )
    for index, disposition in enumerate(("resalable", "damaged", "quarantine"), start=1):
        inspect_item(
            db,
            case_id=case.id,
            item_id=physical.id,
            quantity=1,
            disposition=disposition,
            idempotency_key=f"inspect-disp-{index}",
            actor_admin_id=None,
        )
    db.commit()
    return case, physical, variant


def test_sellable_store_stock_excludes_damaged_and_quarantine():
    row = {
        "meta": {"href": "https://api.example.invalid/entity/variant/variant-disp"},
        "stockByStore": [
            {"meta": {"href": "https://api.example.invalid/entity/store/store-sellable"}, "stock": 7},
            {"meta": {"href": "https://api.example.invalid/entity/store/store-damaged"}, "stock": 4},
            {"meta": {"href": "https://api.example.invalid/entity/store/store-quarantine"}, "stock": 3},
        ],
    }
    assert moysklad_service._sellable_stock_from_store_row(row, "store-sellable") == 7


def test_mixed_return_creates_three_distinct_provider_outcomes(monkeypatch):
    db = _db()
    case, physical, variant = _mixed_case(db)
    monkeypatch.setattr(reverse_moysklad, "get_settings", _settings)

    first = reverse_moysklad.enqueue_moysklad_physical_sales_return(db, case.id)
    db.flush()
    commands = (
        db.query(ProviderCommand)
        .filter(
            ProviderCommand.provider == "moysklad",
            ProviderCommand.aggregate_type == "return_logistics_case",
            ProviderCommand.aggregate_id == str(case.id),
            ProviderCommand.command_type.like("moysklad.physical_sales_return%"),
        )
        .order_by(ProviderCommand.id.asc())
        .all()
    )
    assert first in commands
    assert {row.command_type for row in commands} == {
        "moysklad.physical_sales_return.create",
        "moysklad.physical_sales_return.damaged.create",
        "moysklad.physical_sales_return.quarantine.create",
    }
    stores = {
        json.loads(row.payload_json)["provider_disposition"]:
        json.loads(row.payload_json)["provider_store_id"]
        for row in commands
    }
    assert stores == {
        "resalable": "store-sellable",
        "damaged": "store-damaged",
        "quarantine": "store-quarantine",
    }
    assert any(":resalable:sales_return:v2" in row.idempotency_key for row in commands)
    db.refresh(variant)
    assert variant.stock_qty == 11
    assert physical.resalable_qty == 1
    assert physical.damaged_qty == 1
    assert physical.quarantine_qty == 1


def test_quarantine_resolution_is_idempotent_and_preserves_initial_inspection(monkeypatch):
    db = _db()
    case, physical, variant = _mixed_case(db)
    monkeypatch.setattr(reverse_moysklad, "get_settings", _settings)
    reverse_moysklad.enqueue_moysklad_physical_sales_return(db, case.id)
    db.flush()
    before = reverse_moysklad._build_physical_return_allocation(db, case.id)

    result = resolve_quarantine_item(
        db,
        case_id=case.id,
        item_id=physical.id,
        quantity=1,
        disposition="resalable",
        idempotency_key="resolve-quarantine-1",
        actor_admin_id=None,
        reason="QC passed",
    )
    retry = resolve_quarantine_item(
        db,
        case_id=case.id,
        item_id=physical.id,
        quantity=1,
        disposition="resalable",
        idempotency_key="resolve-quarantine-1",
        actor_admin_id=None,
        reason="QC passed",
    )
    assert retry.idempotent is True
    assert retry.event.id == result.event.id
    command = reverse_moysklad.enqueue_moysklad_quarantine_move(db, result.event.id)
    again = reverse_moysklad.enqueue_moysklad_quarantine_move(db, result.event.id)
    assert again is command
    db.flush()

    after = reverse_moysklad._build_physical_return_allocation(db, case.id)
    assert after == before
    db.refresh(physical)
    db.refresh(variant)
    assert (physical.resalable_qty, physical.damaged_qty, physical.quarantine_qty) == (2, 1, 0)
    assert variant.stock_qty == 12
    assert json.loads(command.payload_json)["source_store_id"] == "store-quarantine"
    assert json.loads(command.payload_json)["target_store_id"] == "store-sellable"
    assert db.query(ReturnLogisticsEvent).filter(
        ReturnLogisticsEvent.case_id == case.id,
        ReturnLogisticsEvent.event_type == "reclassified",
    ).count() == 1


def test_legacy_mixed_v1_command_is_never_silently_replayed(monkeypatch):
    db = _db()
    case, _physical, _variant = _mixed_case(db)
    monkeypatch.setattr(reverse_moysklad, "get_settings", _settings)
    payload = reverse_moysklad._build_physical_return_allocation(db, case.id)
    case_id = int(case.id)
    enqueue_provider_command(
        db,
        provider="moysklad",
        command_type="moysklad.physical_sales_return.create",
        idempotency_key=f"physical-return:{case_id}:sales_return:v1",
        aggregate_type="return_logistics_case",
        aggregate_id=case_id,
        payload=payload,
    )
    db.commit()

    with pytest.raises(MoySkladReviewRequired, match="Legacy mixed-disposition"):
        reverse_moysklad._prepare_physical_return_snapshot(db, case_id, "resalable")


def test_ambiguous_sales_return_transport_is_terminal_review(monkeypatch):
    snapshot = reverse_moysklad._PhysicalReturnSnapshot(
        case_id=9,
        return_request_id=10,
        order_snapshot=SimpleNamespace(),
        demand_external_id="demand",
        disposition="damaged",
        store_id="store-damaged",
        lines=(reverse_moysklad._PhysicalReturnLine(1, "variant", 1, 10000),),
    )
    monkeypatch.setattr(reverse_moysklad, "_require_export_configuration", lambda: None)
    monkeypatch.setattr(reverse_moysklad, "_prepare_physical_return_snapshot", lambda *_args, **_kwargs: snapshot)
    monkeypatch.setattr(
        reverse_moysklad,
        "_base_document",
        lambda *_args, **_kwargs: {},
    )
    monkeypatch.setattr(
        reverse_moysklad,
        "_physical_return_positions",
        lambda _snapshot: asyncio.sleep(0, result=[{"assortment": {}, "quantity": 1, "price": 10000}]),
    )
    monkeypatch.setattr(reverse_moysklad, "_entity_meta", lambda kind, value: {"kind": kind, "id": value})
    monkeypatch.setattr(reverse_moysklad, "get_settings", _settings)

    async def ambiguous(*_args, **_kwargs):
        raise httpx.ReadTimeout("provider response lost")

    monkeypatch.setattr(reverse_moysklad, "_request_json", ambiguous)
    with pytest.raises(MoySkladReviewRequired, match="ambiguous"):
        asyncio.run(reverse_moysklad.export_physical_sales_return(object(), 9, "damaged"))

def test_ambiguous_quarantine_move_transport_is_terminal_review(monkeypatch):
    snapshot = reverse_moysklad._QuarantineMoveSnapshot(
        event_id=12,
        case_id=9,
        moysklad_id="variant",
        quantity=1,
        target_disposition="resalable",
        source_store_id="store-quarantine",
        target_store_id="store-sellable",
    )
    monkeypatch.setattr(reverse_moysklad, "_require_export_configuration", lambda: None)
    monkeypatch.setattr(
        reverse_moysklad,
        "_prepare_quarantine_move_snapshot",
        lambda *_args, **_kwargs: snapshot,
    )

    async def fake_assortment(_moysklad_id):
        return {"href": "https://example.invalid/entity/variant/variant"}

    async def ambiguous(*_args, **_kwargs):
        raise httpx.ReadTimeout("provider response lost")

    monkeypatch.setattr(reverse_moysklad, "_resolve_assortment_meta", fake_assortment)
    monkeypatch.setattr(reverse_moysklad, "_entity_meta", lambda kind, value: {"kind": kind, "id": value})
    monkeypatch.setattr(reverse_moysklad, "get_settings", _settings)
    monkeypatch.setattr(reverse_moysklad, "_request_json", ambiguous)

    with pytest.raises(MoySkladReviewRequired, match="quarantine move outcome is ambiguous"):
        asyncio.run(reverse_moysklad.export_quarantine_move(object(), 12))

