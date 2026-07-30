from __future__ import annotations

import json

from fastapi import HTTPException
from sqlalchemy import CheckConstraint, event

from .models import PaymentEvent
from .payment_statuses import (
    ACTIONABLE_PAYMENT_EVENT_TYPES,
    PERSISTED_PAYMENT_EVENT_TYPE_SQL,
    UNRESOLVED_PAYMENT_EVENT,
)


def _constraint_names() -> set[str]:
    return {constraint.name for constraint in PaymentEvent.__table__.constraints if constraint.name}


def _check(name: str, expression: str) -> None:
    if name not in _constraint_names():
        PaymentEvent.__table__.append_constraint(CheckConstraint(expression, name=name))


def _normalize_before_write(_mapper, _connection, target: PaymentEvent) -> None:
    provider = str(target.provider or "").strip().lower()
    provider_payment_id = str(target.provider_payment_id or "").strip()
    event_type = str(target.event_type or "").strip().lower()
    raw_payload = str(target.raw_payload or "").strip()

    if not provider or provider == "legacy_unresolved" or len(provider) > 64:
        raise HTTPException(status_code=400, detail="Payment event provider is invalid")
    if not provider_payment_id or len(provider_payment_id) > 255:
        raise HTTPException(status_code=400, detail="Payment event payment id is invalid")
    if event_type not in ACTIONABLE_PAYMENT_EVENT_TYPES:
        raise HTTPException(status_code=400, detail="Payment event type is invalid")
    if not raw_payload:
        raise HTTPException(status_code=400, detail="Payment event payload is required")
    try:
        parsed = json.loads(raw_payload)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="Payment event payload must be valid JSON") from exc
    if not isinstance(parsed, dict):
        raise HTTPException(status_code=400, detail="Payment event payload must be a JSON object")

    target.provider = provider
    target.provider_payment_id = provider_payment_id
    target.event_type = event_type
    target.raw_payload = json.dumps(
        parsed,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def apply_payment_event_constraints() -> None:
    _check(
        "ck_payment_events_provider_normalized",
        "length(trim(provider)) > 0 AND provider = lower(trim(provider))",
    )
    _check(
        "ck_payment_events_provider_payment_id_normalized",
        "length(trim(provider_payment_id)) > 0 AND provider_payment_id = trim(provider_payment_id)",
    )
    _check(
        "ck_payment_events_event_type_valid",
        f"event_type IN ({PERSISTED_PAYMENT_EVENT_TYPE_SQL})",
    )
    _check(
        "ck_payment_events_event_type_normalized",
        "event_type = lower(trim(event_type))",
    )
    _check(
        "ck_payment_events_payload_nonempty",
        "length(trim(raw_payload)) > 0",
    )
    _check(
        "ck_payment_events_legacy_state_coherent",
        (
            f"(provider = 'legacy_unresolved' AND event_type = '{UNRESOLVED_PAYMENT_EVENT}' "
            "AND processed = false) OR "
            f"(provider <> 'legacy_unresolved' AND event_type <> '{UNRESOLVED_PAYMENT_EVENT}')"
        ),
    )

    for event_name in ("before_insert", "before_update"):
        if not event.contains(PaymentEvent, event_name, _normalize_before_write):
            event.listen(PaymentEvent, event_name, _normalize_before_write)


apply_payment_event_constraints()
