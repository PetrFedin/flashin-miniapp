#!/usr/bin/env python3
"""Prove durable delivery-provider booking semantics on PostgreSQL."""

from __future__ import annotations

import asyncio
import json
import sys
import uuid
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.database import SessionLocal, engine
from backend.delivery_models import DeliveryZoneRule
from backend.jobs.delivery_provider_jobs import process_delivery_provider_commands
from backend.models import Customer, DeliveryProvider, DeliveryZone, Order
from backend.provider_models import ProviderCommand
from backend.services.delivery_authority import (
    DeliveryAuthorityError,
    accept_quote_for_order,
    create_delivery_quote,
)
from backend.services.delivery_providers import ensure_ready_shipment, transition_shipment


def _fixture(db, token: str, suffix: str, mode: str):
    provider_code = f"{suffix}-{token}"
    provider = DeliveryProvider(
        code=provider_code,
        name=f"{suffix} provider {token}",
        active=True,
        config_json=json.dumps({"mode": mode}, sort_keys=True),
    )
    customer = Customer(
        telegram_id=f"delivery-booking-{suffix}-{token}",
        first_name="Delivery Booking",
    )
    db.add_all([provider, customer])
    db.flush()

    zone = DeliveryZone(
        name=f"{suffix} zone {token}",
        delivery_type="courier",
        price=Decimal("500.00"),
        active=True,
        description=f"{mode} booking fixture",
    )
    db.add(zone)
    db.flush()
    db.add(
        DeliveryZoneRule(
            zone_id=zone.id,
            delivery_type="courier",
            priority={"manual": 710001, "sandbox": 710002, "live": 710003, "disabled": 710004}[mode],
            country_code="RU",
            region="",
            city=f"Тест-{suffix}-{token}",
            postal_prefix="",
            provider_code=provider_code,
            service_code=f"{suffix}-courier",
            currency="RUB",
            version=1,
        )
    )
    db.flush()
    return provider, customer


def _ready_order(db, customer, *, city: str):
    quote = create_delivery_quote(
        db,
        customer_id=customer.id,
        delivery_type="courier",
        city=city,
        address_line="Тестовая улица, 1",
    )
    order = Order(
        customer_id=customer.id,
        status="ready",
        payment_status="paid",
        delivery_status="ready",
        total_amount=Decimal("10500.00"),
        delivery_price=quote.price,
        discount_amount=0,
        loyalty_points_redeemed=0,
        loyalty_discount_amount=0,
        currency="RUB",
        delivery_type="courier",
        address=quote.address_snapshot,
        comment="Delivery booking smoke",
    )
    db.add(order)
    db.flush()
    accept_quote_for_order(quote, order)
    db.flush()
    return order, quote


def _booking_commands(db, shipment_id: int):
    return (
        db.query(ProviderCommand)
        .filter(
            ProviderCommand.provider == "delivery",
            ProviderCommand.command_type == "delivery.shipment.book",
            ProviderCommand.aggregate_type == "delivery_shipment",
            ProviderCommand.aggregate_id == str(shipment_id),
        )
        .order_by(ProviderCommand.id.asc())
        .all()
    )


def main() -> int:
    if engine.dialect.name != "postgresql":
        raise RuntimeError("delivery provider booking smoke requires PostgreSQL")

    token = uuid.uuid4().hex[:10]
    with SessionLocal() as db:
        manual_provider, manual_customer = _fixture(db, token, "manual", "manual")
        manual_order, _ = _ready_order(
            db,
            manual_customer,
            city=f"Тест-manual-{token}",
        )
        manual_shipment, manual_created = ensure_ready_shipment(
            db,
            manual_order,
            manual_provider.code,
        )
        assert manual_created is True
        assert _booking_commands(db, manual_shipment.id) == []
        transition_shipment(db, manual_order, manual_shipment, f"MAN-{token}", "shipped")
        assert manual_shipment.status == "shipped"
        db.commit()

        sandbox_provider, sandbox_customer = _fixture(db, token, "sandbox", "sandbox")
        sandbox_order, _ = _ready_order(
            db,
            sandbox_customer,
            city=f"Тест-sandbox-{token}",
        )
        sandbox_shipment, sandbox_created = ensure_ready_shipment(
            db,
            sandbox_order,
            sandbox_provider.code,
        )
        assert sandbox_created is True
        db.flush()
        sandbox_commands = _booking_commands(db, sandbox_shipment.id)
        assert len(sandbox_commands) == 1
        assert sandbox_commands[0].status == "pending"
        same_shipment, created_again = ensure_ready_shipment(
            db,
            sandbox_order,
            sandbox_provider.code,
        )
        db.flush()
        assert created_again is False
        assert same_shipment.id == sandbox_shipment.id
        assert len(_booking_commands(db, sandbox_shipment.id)) == 1
        try:
            transition_shipment(
                db,
                sandbox_order,
                sandbox_shipment,
                f"SBX-{token}",
                "shipped",
            )
        except ValueError as exc:
            assert "not confirmed" in str(exc)
        else:
            raise AssertionError("Sandbox shipment must wait for durable booking confirmation")
        db.commit()

        sandbox_result = asyncio.run(process_delivery_provider_commands(db, limit=20))
        assert sandbox_result["claimed"] == 1
        assert sandbox_result["sent"] == 1
        db.expire_all()
        sandbox_command = _booking_commands(db, sandbox_shipment.id)[0]
        assert sandbox_command.status == "sent"
        assert sandbox_command.external_id.startswith("sandbox-delivery-")
        sandbox_order = db.query(Order).filter(Order.id == sandbox_order.id).one()
        sandbox_shipment = same_shipment = db.query(type(sandbox_shipment)).filter(type(sandbox_shipment).id == sandbox_shipment.id).one()
        transition_shipment(
            db,
            sandbox_order,
            sandbox_shipment,
            f"SBX-{token}",
            "shipped",
        )
        assert sandbox_shipment.status == "shipped"
        db.commit()

        live_provider, live_customer = _fixture(db, token, "live", "live")
        live_order, _ = _ready_order(
            db,
            live_customer,
            city=f"Тест-live-{token}",
        )
        live_shipment, _ = ensure_ready_shipment(db, live_order, live_provider.code)
        db.commit()

        async def ambiguous_adapter(_payload):
            raise TimeoutError("simulated carrier timeout after request write")

        live_result = asyncio.run(
            process_delivery_provider_commands(
                db,
                limit=20,
                live_adapter=ambiguous_adapter,
            )
        )
        assert live_result["claimed"] == 1
        assert live_result["review_required"] == 1
        live_command = _booking_commands(db, live_shipment.id)[0]
        assert live_command.status == "review_required"
        assert "ambiguous" in live_command.last_error.lower()
        live_order = db.query(Order).filter(Order.id == live_order.id).one()
        live_shipment = db.query(type(live_shipment)).filter(type(live_shipment).id == live_shipment.id).one()
        try:
            transition_shipment(
                db,
                live_order,
                live_shipment,
                f"LIVE-{token}",
                "shipped",
            )
        except ValueError as exc:
            assert "reconciliation" in str(exc)
        else:
            raise AssertionError("Ambiguous live booking must block shipment")

        disabled_provider, disabled_customer = _fixture(db, token, "disabled", "disabled")
        try:
            create_delivery_quote(
                db,
                customer_id=disabled_customer.id,
                delivery_type="courier",
                city=f"Тест-disabled-{token}",
                address_line="Тестовая улица, 1",
            )
        except DeliveryAuthorityError as exc:
            assert exc.code == "provider_unavailable"
            disabled_state = exc.code
        else:
            raise AssertionError("Disabled provider must not mint a delivery quote")

        db.rollback()

    print(
        json.dumps(
            {
                "status": "ok",
                "manual_booking": "not_applicable",
                "sandbox_commands": 1,
                "sandbox_external_id": sandbox_command.external_id,
                "live_timeout": "review_required",
                "disabled_provider": disabled_state,
                "fake_live_success": False,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
