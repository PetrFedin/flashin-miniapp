#!/usr/bin/env python3
"""Prove delivery commercial authority against real PostgreSQL state.

This smoke is intentionally provider-network free. It proves that local
commercial truth is deterministic and immutable before any optional carrier
adapter is enabled:

Address -> Zone -> Service/Provider -> DeliveryQuote -> Order -> Shipment.
"""

from __future__ import annotations

import json
import sys
import uuid
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.database import SessionLocal, engine
from backend.delivery_models import DeliveryShipmentAuthority, DeliveryZoneRule
from backend.models import Customer, DeliveryProvider, DeliveryZone, Order
from backend.services.delivery_authority import (
    DeliveryAuthorityError,
    accept_quote_for_order,
    create_delivery_quote,
    lock_checkout_quote,
)
from backend.services.delivery_providers import ensure_ready_shipment

_MONEY = Decimal("0.01")


def _amount(value: object) -> Decimal:
    return Decimal(str(value)).quantize(_MONEY)


def _expect_error(code: str, callback) -> str:
    try:
        callback()
    except DeliveryAuthorityError as exc:
        assert exc.code == code, (code, exc.code, str(exc))
        return exc.code
    raise AssertionError(f"Expected DeliveryAuthorityError({code})")


def main() -> int:
    if engine.dialect.name != "postgresql":
        raise RuntimeError("delivery authority smoke requires PostgreSQL")

    token = uuid.uuid4().hex[:12]
    provider_code = f"manual-{token}"

    with SessionLocal() as db:
        provider = DeliveryProvider(
            code=provider_code,
            name=f"Manual courier {token}",
            active=True,
            config_json=json.dumps({"mode": "manual"}, sort_keys=True),
        )
        customer = Customer(
            telegram_id=f"delivery-authority-{token}",
            first_name="Delivery Authority",
        )
        db.add_all([provider, customer])
        db.flush()

        moscow = DeliveryZone(
            name=f"Moscow {token}",
            delivery_type="courier",
            price=Decimal("500.00"),
            active=True,
            description="Moscow authority fixture",
        )
        petersburg = DeliveryZone(
            name=f"Petersburg {token}",
            delivery_type="courier",
            price=Decimal("700.00"),
            active=True,
            description="Petersburg authority fixture",
        )
        db.add_all([moscow, petersburg])
        db.flush()
        db.add_all(
            [
                DeliveryZoneRule(
                    zone_id=moscow.id,
                    delivery_type="courier",
                    priority=100,
                    country_code="RU",
                    region="",
                    city="Москва",
                    postal_prefix="",
                    provider_code=provider_code,
                    service_code="manual-courier",
                    currency="RUB",
                    version=1,
                ),
                DeliveryZoneRule(
                    zone_id=petersburg.id,
                    delivery_type="courier",
                    priority=200,
                    country_code="RU",
                    region="",
                    city="Санкт-Петербург",
                    postal_prefix="",
                    provider_code=provider_code,
                    service_code="manual-courier",
                    currency="RUB",
                    version=1,
                ),
            ]
        )
        db.flush()

        quote_moscow = create_delivery_quote(
            db,
            customer_id=customer.id,
            delivery_type="courier",
            city="Москва",
            address_line="Тверская улица, 1",
        )
        quote_petersburg = create_delivery_quote(
            db,
            customer_id=customer.id,
            delivery_type="courier",
            city="Санкт-Петербург",
            address_line="Невский проспект, 1",
        )
        assert quote_moscow.zone_id == moscow.id
        assert quote_petersburg.zone_id == petersburg.id
        assert _amount(quote_moscow.price) == Decimal("500.00")
        assert _amount(quote_petersburg.price) == Decimal("700.00")
        assert quote_moscow.provider_code == provider_code
        assert quote_petersburg.provider_code == provider_code

        unsupported = _expect_error(
            "unsupported_address",
            lambda: create_delivery_quote(
                db,
                customer_id=customer.id,
                delivery_type="courier",
                city="Казань",
                address_line="Кремлёвская улица, 1",
            ),
        )

        # A quote is immutable commercial evidence. Changing the tariff after
        # display must force an explicit re-quote rather than silently drifting.
        stale_quote = create_delivery_quote(
            db,
            customer_id=customer.id,
            delivery_type="courier",
            city="Москва",
            address_line="Тверская улица, 2",
        )
        moscow.price = Decimal("550.00")
        db.flush()
        stale = _expect_error(
            "quote_stale",
            lambda: lock_checkout_quote(
                db,
                customer_id=customer.id,
                quote_public_id=stale_quote.public_id,
                delivery_type="courier",
                submitted_address=stale_quote.address_snapshot,
            ),
        )

        # The already-created Petersburg quote is still current because only
        # Moscow changed. Bind it to one Order and prove Shipment cannot invent
        # another provider or amount.
        locked_quote = lock_checkout_quote(
            db,
            customer_id=customer.id,
            quote_public_id=quote_petersburg.public_id,
            delivery_type="courier",
            submitted_address=quote_petersburg.address_snapshot,
        )
        order = Order(
            customer_id=customer.id,
            status="ready",
            payment_status="paid",
            delivery_status="ready",
            total_amount=Decimal("10700.00"),
            delivery_price=locked_quote.price,
            discount_amount=0,
            loyalty_points_redeemed=0,
            loyalty_discount_amount=0,
            currency="RUB",
            delivery_type="courier",
            address=locked_quote.address_snapshot,
            comment="Delivery authority invariant smoke",
        )
        db.add(order)
        db.flush()
        accept_quote_for_order(locked_quote, order)
        db.flush()

        shipment, created = ensure_ready_shipment(db, order, provider_code)
        assert created is True
        same_shipment, created_again = ensure_ready_shipment(db, order, provider_code)
        assert created_again is False
        assert same_shipment.id == shipment.id
        assert shipment.provider_code == locked_quote.provider_code
        assert _amount(shipment.price) == _amount(order.delivery_price) == _amount(locked_quote.price)

        authority = (
            db.query(DeliveryShipmentAuthority)
            .filter(DeliveryShipmentAuthority.shipment_id == shipment.id)
            .one()
        )
        assert authority.order_id == order.id
        assert authority.quote_id == locked_quote.id
        assert authority.provider_code == locked_quote.provider_code
        assert _amount(authority.price) == _amount(locked_quote.price)

        try:
            ensure_ready_shipment(db, order, "different-provider")
        except ValueError as exc:
            assert "accepted delivery quote" in str(exc)
            provider_mismatch = "rejected"
        else:
            raise AssertionError("Shipment provider drift must be rejected")

        # Provider disablement is an explicit unavailable state, never a fake
        # successful quote. Platform readiness is tested separately and must not
        # depend on an intentionally disabled external carrier.
        provider.active = False
        db.flush()
        provider_disabled = _expect_error(
            "provider_unavailable",
            lambda: create_delivery_quote(
                db,
                customer_id=customer.id,
                delivery_type="courier",
                city="Санкт-Петербург",
                address_line="Невский проспект, 2",
            ),
        )

        db.rollback()

    print(
        json.dumps(
            {
                "status": "ok",
                "zones": {
                    "moscow": "500.00",
                    "petersburg": "700.00",
                },
                "unsupported_address": unsupported,
                "stale_quote": stale,
                "provider_mismatch": provider_mismatch,
                "provider_disabled": provider_disabled,
                "shipment_booking": "idempotent-local-authority",
                "external_provider_calls": 0,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
