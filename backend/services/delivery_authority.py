from __future__ import annotations

import json
import secrets
import unicodedata
from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy.orm import Session

from ..database import utcnow_naive
from ..delivery_models import DeliveryQuote, DeliveryShipmentAuthority, DeliveryZoneRule
from ..models import DeliveryProvider, DeliveryShipment, DeliveryZone, Order

_MONEY_STEP = Decimal("0.01")
_QUOTE_TTL_MINUTES = 30


class DeliveryAuthorityError(ValueError):
    def __init__(self, message: str, *, code: str = "delivery_authority_error"):
        super().__init__(message)
        self.code = code


def _text(value: object, limit: int) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or "")).strip()
    normalized = " ".join(normalized.split())
    return normalized[:limit]


def _match_text(value: object, limit: int) -> str:
    return _text(value, limit).casefold()


def _money(value: object) -> Decimal:
    amount = value if isinstance(value, Decimal) else Decimal(str(value or 0))
    if not amount.is_finite():
        raise DeliveryAuthorityError("Delivery price is invalid", code="invalid_price")
    return amount.quantize(_MONEY_STEP, rounding=ROUND_HALF_UP)


def _provider_is_active(db: Session, provider_code: str) -> bool:
    if provider_code == "pickup":
        return True
    row = (
        db.query(DeliveryProvider)
        .filter(DeliveryProvider.code == provider_code, DeliveryProvider.active.is_(True))
        .first()
    )
    return row is not None


def _address_snapshot(*, country_code: str, region: str, city: str, postal_code: str, address_line: str) -> str:
    chunks = [postal_code, city, region, address_line]
    rendered = ", ".join(value for value in chunks if value)
    if country_code and country_code != "RU":
        rendered = f"{country_code}, {rendered}" if rendered else country_code
    return rendered[:700]


def _rule_matches(rule: DeliveryZoneRule, *, country_code: str, region: str, city: str, postal_code: str) -> bool:
    if rule.country_code and _match_text(rule.country_code, 2) != _match_text(country_code, 2):
        return False
    if rule.region and _match_text(rule.region, 160) != _match_text(region, 160):
        return False
    if rule.city and _match_text(rule.city, 160) != _match_text(city, 160):
        return False
    if rule.postal_prefix and not _match_text(postal_code, 32).startswith(_match_text(rule.postal_prefix, 32)):
        return False
    return True


def _resolve_zone(
    db: Session,
    *,
    delivery_type: str,
    country_code: str,
    region: str,
    city: str,
    postal_code: str,
) -> tuple[DeliveryZone, DeliveryZoneRule]:
    rows = (
        db.query(DeliveryZone, DeliveryZoneRule)
        .join(DeliveryZoneRule, DeliveryZoneRule.zone_id == DeliveryZone.id)
        .filter(
            DeliveryZone.active.is_(True),
            DeliveryZone.delivery_type == delivery_type,
            DeliveryZoneRule.delivery_type == delivery_type,
        )
        .order_by(DeliveryZoneRule.priority.asc(), DeliveryZone.id.asc())
        .all()
    )
    for zone, rule in rows:
        if _rule_matches(
            rule,
            country_code=country_code,
            region=region,
            city=city,
            postal_code=postal_code,
        ):
            if not _provider_is_active(db, rule.provider_code):
                raise DeliveryAuthorityError(
                    "Delivery service is temporarily unavailable",
                    code="provider_unavailable",
                )
            return zone, rule
    raise DeliveryAuthorityError(
        "Delivery is not available for this address",
        code="unsupported_address",
    )


def create_delivery_quote(
    db: Session,
    *,
    customer_id: int,
    delivery_type: str,
    country_code: str = "RU",
    region: str = "",
    city: str = "",
    postal_code: str = "",
    address_line: str = "",
) -> DeliveryQuote:
    normalized_type = _match_text(delivery_type, 64)
    if normalized_type not in {"pickup", "courier"}:
        raise DeliveryAuthorityError("Unsupported delivery type", code="unsupported_delivery_type")

    country = _text(country_code or "RU", 2).upper()
    normalized_region = _text(region, 160)
    normalized_city = _text(city, 160)
    normalized_postal = _text(postal_code, 32)
    normalized_line = _text(address_line, 500)

    if normalized_type == "pickup":
        zone = None
        rule = None
        provider_code = "pickup"
        service_code = "pickup"
        currency = "RUB"
        price = Decimal("0.00")
        snapshot = "Самовывоз"
        zone_version = 0
    else:
        if len(normalized_city) < 2 or len(normalized_line) < 5:
            raise DeliveryAuthorityError(
                "City and full street address are required for courier delivery",
                code="address_incomplete",
            )
        zone, rule = _resolve_zone(
            db,
            delivery_type=normalized_type,
            country_code=country,
            region=normalized_region,
            city=normalized_city,
            postal_code=normalized_postal,
        )
        provider_code = rule.provider_code
        service_code = rule.service_code
        currency = rule.currency
        price = _money(zone.price)
        snapshot = _address_snapshot(
            country_code=country,
            region=normalized_region,
            city=normalized_city,
            postal_code=normalized_postal,
            address_line=normalized_line,
        )
        zone_version = int(rule.version)

    now = utcnow_naive()
    quote = DeliveryQuote(
        public_id=f"dq_{secrets.token_urlsafe(24)}",
        customer_id=customer_id,
        zone_id=zone.id if zone else None,
        delivery_type=normalized_type,
        country_code=country,
        region=normalized_region,
        city=normalized_city,
        postal_code=normalized_postal,
        address_line=normalized_line,
        address_snapshot=snapshot,
        provider_code=provider_code,
        service_code=service_code,
        price=price,
        currency=currency,
        zone_version=zone_version,
        quote_version=1,
        status="created",
        expires_at=now + timedelta(minutes=_QUOTE_TTL_MINUTES),
    )
    db.add(quote)
    db.flush()
    return quote


def _expire_if_needed(quote: DeliveryQuote) -> None:
    if quote.status == "created" and quote.expires_at <= utcnow_naive():
        quote.status = "expired"
        raise DeliveryAuthorityError("Delivery quote expired; request a new quote", code="quote_expired")


def lock_checkout_quote(
    db: Session,
    *,
    customer_id: int,
    quote_public_id: str,
    delivery_type: str,
    submitted_address: str,
) -> DeliveryQuote:
    quote = (
        db.query(DeliveryQuote)
        .filter(
            DeliveryQuote.public_id == str(quote_public_id or "").strip(),
            DeliveryQuote.customer_id == customer_id,
        )
        .with_for_update()
        .first()
    )
    if quote is None:
        raise DeliveryAuthorityError("Delivery quote not found", code="quote_not_found")
    _expire_if_needed(quote)
    if quote.status != "created":
        raise DeliveryAuthorityError("Delivery quote was already consumed", code="quote_consumed")
    if quote.delivery_type != _match_text(delivery_type, 64):
        raise DeliveryAuthorityError("Delivery type changed; request a new quote", code="quote_mismatch")

    if quote.delivery_type == "courier":
        if _match_text(submitted_address, 700) != _match_text(quote.address_snapshot, 700):
            raise DeliveryAuthorityError("Delivery address changed; request a new quote", code="quote_mismatch")
        zone = (
            db.query(DeliveryZone)
            .filter(DeliveryZone.id == quote.zone_id)
            .with_for_update()
            .first()
        )
        rule = (
            db.query(DeliveryZoneRule)
            .filter(DeliveryZoneRule.zone_id == quote.zone_id)
            .with_for_update()
            .first()
        )
        if zone is None or rule is None or not zone.active:
            raise DeliveryAuthorityError("Delivery tariff is no longer available; request a new quote", code="quote_stale")
        if (
            rule.delivery_type != quote.delivery_type
            or int(rule.version) != int(quote.zone_version)
            or rule.provider_code != quote.provider_code
            or rule.service_code != quote.service_code
            or rule.currency != quote.currency
            or _money(zone.price) != _money(quote.price)
        ):
            raise DeliveryAuthorityError("Delivery tariff changed; request a new quote", code="quote_stale")
        if not _provider_is_active(db, quote.provider_code):
            raise DeliveryAuthorityError("Delivery service is temporarily unavailable", code="provider_unavailable")
    elif _match_text(submitted_address, 700):
        raise DeliveryAuthorityError("Pickup quote cannot be used with a courier address", code="quote_mismatch")

    return quote


def accept_quote_for_order(quote: DeliveryQuote, order: Order) -> None:
    if quote.status != "created" or quote.order_id is not None:
        raise DeliveryAuthorityError("Delivery quote was already consumed", code="quote_consumed")
    if int(quote.customer_id) != int(order.customer_id):
        raise DeliveryAuthorityError("Delivery quote owner mismatch", code="quote_owner_mismatch")
    if _money(order.delivery_price) != _money(quote.price):
        raise DeliveryAuthorityError("Order delivery price differs from accepted quote", code="quote_price_mismatch")
    quote.status = "accepted"
    quote.accepted_at = utcnow_naive()
    quote.order_id = order.id


def accepted_quote_for_order(db: Session, order_id: int, *, lock: bool = False) -> DeliveryQuote:
    query = db.query(DeliveryQuote).filter(
        DeliveryQuote.order_id == order_id,
        DeliveryQuote.status == "accepted",
    )
    if lock:
        query = query.with_for_update()
    quote = query.first()
    if quote is None:
        raise DeliveryAuthorityError("Order has no accepted delivery quote", code="order_quote_missing")
    return quote


def bind_shipment_commercial_authority(
    db: Session,
    *,
    shipment: DeliveryShipment,
    order: Order,
    quote: DeliveryQuote,
) -> DeliveryShipmentAuthority:
    if quote.order_id != order.id or quote.status != "accepted":
        raise DeliveryAuthorityError("Shipment quote is not the accepted order quote", code="shipment_quote_invalid")
    if quote.provider_code != shipment.provider_code:
        raise DeliveryAuthorityError("Shipment provider differs from accepted quote", code="shipment_provider_mismatch")
    if _money(order.delivery_price) != _money(quote.price):
        raise DeliveryAuthorityError("Order delivery price drift detected", code="shipment_price_mismatch")

    shipment.price = _money(quote.price)
    snapshot = {
        "quote_public_id": quote.public_id,
        "zone_id": quote.zone_id,
        "zone_version": quote.zone_version,
        "provider_code": quote.provider_code,
        "service_code": quote.service_code,
        "price": str(_money(quote.price)),
        "currency": quote.currency,
        "address_snapshot": quote.address_snapshot,
    }
    authority = DeliveryShipmentAuthority(
        shipment_id=shipment.id,
        quote_id=quote.id,
        order_id=order.id,
        zone_id=quote.zone_id,
        provider_code=quote.provider_code,
        service_code=quote.service_code,
        price=_money(quote.price),
        currency=quote.currency,
        snapshot_json=json.dumps(snapshot, ensure_ascii=False, sort_keys=True),
    )
    db.add(authority)
    db.flush()
    return authority
