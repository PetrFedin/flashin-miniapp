from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any, Mapping

from sqlalchemy.orm import Session

from ..models import Order, Payment, ReturnRequest
from ..pilot_models import PilotOrderSlot, PilotRuntimeState

MONEY_TOLERANCE = Decimal("0.01")
DATABASE_EVIDENCE_CONTRACT = 1


def _decimal(value: object) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _positive_order_id(value: object, number: int, errors: list[str]) -> int | None:
    raw = str(value or "").strip()
    try:
        parsed = int(raw)
    except ValueError:
        errors.append(
            f"#{number}: order_id must be a positive PostgreSQL order identifier"
        )
        return None
    if parsed < 1 or raw != str(parsed):
        errors.append(f"#{number}: order_id must be a canonical positive integer")
        return None
    return parsed


def _one_payment(
    db: Session,
    raw_identifier: object,
    number: int,
    errors: list[str],
) -> Payment | None:
    raw = str(raw_identifier or "").strip()
    if not raw:
        errors.append(f"#{number}: payment_id is missing from database evidence")
        return None
    if raw.startswith("db:"):
        try:
            payment_id = int(raw[3:])
        except ValueError:
            errors.append(
                f"#{number}: payment_id db: prefix must contain a positive integer"
            )
            return None
        matches = db.query(Payment).filter(Payment.id == payment_id).all()
    else:
        matches = (
            db.query(Payment)
            .filter(Payment.provider_payment_id == raw)
            .all()
        )
    if len(matches) != 1:
        errors.append(
            f"#{number}: payment_id {raw!r} resolved to {len(matches)} "
            "PostgreSQL rows; expected exactly one"
        )
        return None
    return matches[0]


def _one_refund(
    db: Session,
    raw_identifier: object,
    number: int,
    errors: list[str],
) -> ReturnRequest | None:
    raw = str(raw_identifier or "").strip()
    if not raw:
        errors.append(f"#{number}: refund_id is missing from database evidence")
        return None
    if raw.startswith("db:"):
        try:
            refund_id = int(raw[3:])
        except ValueError:
            errors.append(
                f"#{number}: refund_id db: prefix must contain a positive integer"
            )
            return None
        matches = db.query(ReturnRequest).filter(ReturnRequest.id == refund_id).all()
    else:
        matches = (
            db.query(ReturnRequest)
            .filter(ReturnRequest.provider_refund_id == raw)
            .all()
        )
    if len(matches) != 1:
        errors.append(
            f"#{number}: refund_id {raw!r} resolved to {len(matches)} "
            "PostgreSQL rows; expected exactly one"
        )
        return None
    return matches[0]


def _money_matches(left: object, right: object) -> bool:
    first = _decimal(left)
    second = _decimal(right)
    return (
        first is not None
        and second is not None
        and abs(first - second) <= MONEY_TOLERANCE
    )


def validate_pilot_database_evidence(
    db: Session,
    pilot_state: Mapping[str, Any],
    runtime_state: PilotRuntimeState | None,
    *,
    final: bool = False,
) -> list[str]:
    """Verify signed scenario claims against the exact PostgreSQL pilot run."""
    errors: list[str] = []
    if pilot_state.get("schema_version") != 6:
        return ["pilot control state schema is not database-bound"]
    if (
        pilot_state.get("database_evidence_contract")
        != DATABASE_EVIDENCE_CONTRACT
    ):
        return ["pilot database evidence contract is missing or unsupported"]

    scenarios = pilot_state.get("scenarios")
    if not isinstance(scenarios, list) or len(scenarios) != 20:
        return ["pilot database evidence requires exactly 20 scenario records"]
    passed = [
        record
        for record in scenarios
        if isinstance(record, Mapping) and record.get("result") == "pass"
    ]

    if runtime_state is None:
        if passed or final:
            return ["pilot runtime state is missing for database evidence validation"]
        return []

    slots = (
        db.query(PilotOrderSlot)
        .filter(PilotOrderSlot.run_id == runtime_state.run_id)
        .order_by(PilotOrderSlot.sequence.asc())
        .all()
        if runtime_state.run_id
        else []
    )
    if len(slots) != runtime_state.accepted_orders:
        errors.append(
            f"pilot runtime accepted_orders={runtime_state.accepted_orders} but "
            f"PostgreSQL contains {len(slots)} slots"
        )
    slot_by_sequence = {int(slot.sequence): slot for slot in slots}
    if len(slot_by_sequence) != len(slots):
        errors.append("pilot runtime contains duplicate slot sequences")

    admission = pilot_state.get("admission")
    state_admission_sha = (
        str(admission.get("manifest_sha256", ""))
        if isinstance(admission, Mapping)
        else ""
    )
    if passed and runtime_state.status not in {"active", "stopped", "completed"}:
        errors.append("passed pilot scenarios require an armed or completed runtime")
    if passed and runtime_state.admission_sha256 != state_admission_sha:
        errors.append("pilot scenario admission does not match the PostgreSQL runtime")

    passed_order_ids: list[int] = []
    passed_numbers: list[int] = []
    for record in passed:
        try:
            number = int(record.get("number", 0) or 0)
        except (TypeError, ValueError):
            number = 0
        if number < 1 or number > 20:
            errors.append(f"invalid passed scenario number: {number}")
            continue
        passed_numbers.append(number)
        order_id = _positive_order_id(record.get("order_id"), number, errors)
        if order_id is None:
            continue
        passed_order_ids.append(order_id)

        slot = slot_by_sequence.get(number)
        if slot is None:
            errors.append(
                f"#{number}: no PostgreSQL pilot slot exists for this scenario sequence"
            )
        else:
            if slot.order_id != order_id:
                errors.append(
                    f"#{number}: signed order_id={order_id} does not match "
                    f"pilot slot order_id={slot.order_id}"
                )
            if slot.admission_sha256 != runtime_state.admission_sha256:
                errors.append(
                    f"#{number}: pilot slot admission checksum does not match the runtime"
                )

        order = db.query(Order).filter(Order.id == order_id).first()
        if order is None:
            errors.append(f"#{number}: PostgreSQL order {order_id} does not exist")
            continue
        if slot is not None and slot.customer_id != order.customer_id:
            errors.append(
                f"#{number}: pilot slot customer does not own PostgreSQL order {order_id}"
            )

        recorded_order_status = str(record.get("order_status") or "").strip()
        if recorded_order_status != str(order.status):
            errors.append(
                f"#{number}: signed order_status={recorded_order_status!r} does not "
                f"match PostgreSQL {order.status!r}"
            )
        if record.get("expected_amount") not in (None, "") and not _money_matches(
            record.get("expected_amount"), order.total_amount
        ):
            errors.append(
                f"#{number}: signed expected_amount does not match PostgreSQL "
                "order total_amount"
            )
        order_currency = str(order.currency or "").upper()
        recorded_currency = str(record.get("currency") or "").upper()
        provider_currency = str(record.get("provider_currency") or "").upper()
        if recorded_currency and recorded_currency != order_currency:
            errors.append(
                f"#{number}: signed currency does not match PostgreSQL order currency"
            )
        if provider_currency and provider_currency != order_currency:
            errors.append(
                f"#{number}: signed provider_currency does not match PostgreSQL "
                "order currency"
            )

        if (
            record.get("payment_id") not in (None, "")
            or record.get("payment_status") not in (None, "")
        ):
            payment = _one_payment(db, record.get("payment_id"), number, errors)
            if payment is not None:
                if payment.order_id != order_id:
                    errors.append(
                        f"#{number}: PostgreSQL payment belongs to another order"
                    )
                if str(record.get("payment_status") or "") != str(payment.status):
                    errors.append(
                        f"#{number}: signed payment_status does not match PostgreSQL "
                        "payment status"
                    )
                if (
                    record.get("provider_amount") not in (None, "")
                    and not _money_matches(record.get("provider_amount"), payment.amount)
                ):
                    errors.append(
                        f"#{number}: signed provider_amount does not match PostgreSQL "
                        "payment amount"
                    )

        if (
            record.get("refund_id") not in (None, "")
            or record.get("refund_status") not in (None, "")
        ):
            refund = _one_refund(db, record.get("refund_id"), number, errors)
            if refund is not None:
                if refund.order_id != order_id:
                    errors.append(
                        f"#{number}: PostgreSQL refund belongs to another order"
                    )
                if str(record.get("refund_status") or "") != str(refund.status):
                    errors.append(
                        f"#{number}: signed refund_status does not match PostgreSQL "
                        "refund status"
                    )
                if (
                    record.get("provider_amount") not in (None, "")
                    and refund.refund_amount
                    and _decimal(record.get("provider_amount")) is not None
                    and _decimal(record.get("provider_amount"))
                    < _decimal(refund.refund_amount)
                ):
                    errors.append(
                        f"#{number}: signed provider amount is lower than PostgreSQL "
                        "refund amount"
                    )

    if len(passed_numbers) != len(set(passed_numbers)):
        errors.append("passed pilot scenarios contain duplicate sequence numbers")
    if len(passed_order_ids) != len(set(passed_order_ids)):
        errors.append("passed pilot scenarios reuse a PostgreSQL order identifier")

    if final:
        if runtime_state.status != "completed":
            errors.append("final GO requires PostgreSQL pilot runtime status completed")
        if runtime_state.max_orders != 20 or runtime_state.accepted_orders != 20:
            errors.append(
                "final GO requires exactly 20 accepted PostgreSQL pilot orders"
            )
        if len(slots) != 20 or set(slot_by_sequence) != set(range(1, 21)):
            errors.append(
                "final GO requires the complete PostgreSQL pilot slot sequence 1..20"
            )
        expected_order_ids = [
            slot_by_sequence[number].order_id
            for number in range(1, 21)
            if number in slot_by_sequence
        ]
        if (
            len(passed) != 20
            or passed_numbers != list(range(1, 21))
            or passed_order_ids != expected_order_ids
        ):
            errors.append(
                "final GO scenario order IDs do not exactly match the ordered "
                "20 pilot slots"
            )

    return list(dict.fromkeys(errors))
