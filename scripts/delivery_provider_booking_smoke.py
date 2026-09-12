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
from backend.jobs import delivery_provider_jobs
from backend.models import Customer, DeliveryProvider, DeliveryZone, Order
from backend.provider_models import ProviderCommand
from backend.services.delivery_authority import (
    DeliveryAuthorityError,
    accept_quote_for_order,
    create_delivery_quote,
)
from backend.services.delivery_provider_runtime import reconcile_delivery_booking
from backend.services.delivery_providers import ensure_ready_shipment, transition_shipment


def _fixture(db, token: str, suffix: str, mode: str, *, priority: int):
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
            priority=priority,
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


def _run_ambiguous_worker(db):
    async def ambiguous_adapter(_payload):
        await asyncio.sleep(0.05)
        return "late-provider-success-must-not-be-recorded"

    original_timeout = delivery_provider_jobs.DELIVERY_BOOKING_TIMEOUT_SECONDS
    delivery_provider_jobs.DELIVERY_BOOKING_TIMEOUT_SECONDS = 0.01
    try:
        return asyncio.run(
            delivery_provider_jobs.process_delivery_provider_commands(
                db,
                limit=20,
                live_adapter=ambiguous_adapter,
            )
        )
    finally:
        delivery_provider_jobs.DELIVERY_BOOKING_TIMEOUT_SECONDS = original_timeout


def main() -> int:
    if engine.dialect.name != "postgresql":
        raise RuntimeError("delivery provider booking smoke requires PostgreSQL")

    token = uuid.uuid4().hex[:10]
    with SessionLocal() as db:
        manual_provider, manual_customer = _fixture(
            db,
            token,
            "manual",
            "manual",
            priority=710001,
        )
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

        sandbox_provider, sandbox_customer = _fixture(
            db,
            token,
            "sandbox",
            "sandbox",
            priority=710002,
        )
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

        sandbox_result = asyncio.run(delivery_provider_jobs.process_delivery_provider_commands(db, limit=20))
        assert sandbox_result["claimed"] == 1
        assert sandbox_result["sent"] == 1
        db.expire_all()
        sandbox_command = _booking_commands(db, sandbox_shipment.id)[0]
        assert sandbox_command.status == "sent"
        assert sandbox_command.external_id.startswith("sandbox-delivery-")
        sandbox_external_id = str(sandbox_command.external_id)
        sandbox_order = db.query(Order).filter(Order.id == sandbox_order.id).one()
        sandbox_shipment = db.query(type(sandbox_shipment)).filter(type(sandbox_shipment).id == sandbox_shipment.id).one()
        transition_shipment(
            db,
            sandbox_order,
            sandbox_shipment,
            f"SBX-{token}",
            "shipped",
        )
        assert sandbox_shipment.status == "shipped"
        db.commit()

        # Ambiguous live timeout: operator confirms that the carrier did create
        # the booking. Reconciliation updates the same durable command and only
        # then may the shipment leave FLASHIN.
        live_provider, live_customer = _fixture(
            db,
            token,
            "live-confirm",
            "live",
            priority=710003,
        )
        live_order, _ = _ready_order(
            db,
            live_customer,
            city=f"Тест-live-confirm-{token}",
        )
        live_shipment, _ = ensure_ready_shipment(db, live_order, live_provider.code)
        db.commit()

        live_result = _run_ambiguous_worker(db)
        assert live_result["claimed"] == 1
        assert live_result["review_required"] == 1
        live_command = _booking_commands(db, live_shipment.id)[0]
        live_command_id = int(live_command.id)
        live_idempotency_key = str(live_command.idempotency_key)
        assert live_command.status == "review_required"
        assert not live_command.external_id
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

        confirmed_external_id = f"carrier-confirmed-{token}"
        reconciled = reconcile_delivery_booking(
            db,
            shipment_id=live_shipment.id,
            decision="confirmed",
            external_id=confirmed_external_id,
        )
        assert int(reconciled.id) == live_command_id
        assert reconciled.idempotency_key == live_idempotency_key
        assert reconciled.status == "sent"
        assert reconciled.external_id == confirmed_external_id
        assert len(_booking_commands(db, live_shipment.id)) == 1
        transition_shipment(
            db,
            live_order,
            live_shipment,
            f"LIVE-{token}",
            "shipped",
        )
        assert live_shipment.status == "shipped"
        db.commit()

        # Second ambiguous booking: operator proves no carrier booking exists.
        # FLASHIN may retry, but only by returning the SAME durable command to
        # pending. A second booking command/idempotency identity is forbidden.
        retry_provider, retry_customer = _fixture(
            db,
            token,
            "live-retry",
            "live",
            priority=710004,
        )
        retry_order, _ = _ready_order(
            db,
            retry_customer,
            city=f"Тест-live-retry-{token}",
        )
        retry_shipment, _ = ensure_ready_shipment(db, retry_order, retry_provider.code)
        db.commit()

        retry_result = _run_ambiguous_worker(db)
        assert retry_result["claimed"] == 1
        assert retry_result["review_required"] == 1
        retry_command = _booking_commands(db, retry_shipment.id)[0]
        retry_command_id = int(retry_command.id)
        retry_idempotency_key = str(retry_command.idempotency_key)
        retry_attempts_before = int(retry_command.attempts)
        assert retry_command.status == "review_required"

        reconciled_retry = reconcile_delivery_booking(
            db,
            shipment_id=retry_shipment.id,
            decision="not_booked",
        )
        assert int(reconciled_retry.id) == retry_command_id
        assert reconciled_retry.idempotency_key == retry_idempotency_key
        assert reconciled_retry.status == "pending"
        assert int(reconciled_retry.attempts) == retry_attempts_before
        assert len(_booking_commands(db, retry_shipment.id)) == 1
        db.commit()

        async def successful_live_adapter(_payload):
            return f"carrier-retry-success-{token}"

        retry_after_reconciliation = asyncio.run(
            delivery_provider_jobs.process_delivery_provider_commands(
                db,
                limit=20,
                live_adapter=successful_live_adapter,
            )
        )
        assert retry_after_reconciliation["claimed"] == 1
        assert retry_after_reconciliation["sent"] == 1
        db.expire_all()
        retry_command = _booking_commands(db, retry_shipment.id)[0]
        assert int(retry_command.id) == retry_command_id
        assert retry_command.idempotency_key == retry_idempotency_key
        assert retry_command.status == "sent"
        assert retry_command.external_id == f"carrier-retry-success-{token}"
        assert len(_booking_commands(db, retry_shipment.id)) == 1
        retry_order = db.query(Order).filter(Order.id == retry_order.id).one()
        retry_shipment = db.query(type(retry_shipment)).filter(type(retry_shipment).id == retry_shipment.id).one()
        transition_shipment(
            db,
            retry_order,
            retry_shipment,
            f"RETRY-{token}",
            "shipped",
        )
        assert retry_shipment.status == "shipped"
        db.commit()

        disabled_provider, disabled_customer = _fixture(
            db,
            token,
            "disabled",
            "disabled",
            priority=710005,
        )
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
                "sandbox_external_id": sandbox_external_id,
                "live_timeout": "review_required",
                "confirmed_reconciliation": confirmed_external_id,
                "not_booked_reconciliation": "same_command_retried",
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
