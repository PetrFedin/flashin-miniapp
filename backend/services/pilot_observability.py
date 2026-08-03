from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any, Mapping, TYPE_CHECKING

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from ..models import Order, PaymentReconciliation, ReturnRequest
from ..pilot_models import PilotOrderSlot, PilotRuntimeState
from .pilot_runtime import validate_runtime_files

if TYPE_CHECKING:
    from ..config import Settings

_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_SAFE_AUTO_STOP_REASON = re.compile(r"^auto:[a-z0-9:._-]{1,160}$")
_PAYMENT_REVIEW_STATUSES = {"paid_review_required", "payment_review_required"}
_REFUND_ATTENTION_STATUSES = {"refund_retry_required", "refund_review_required"}


def _timestamp(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _generated_at() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _valid_sha256(value: object) -> bool:
    return bool(_HEX64.fullmatch(str(value or "").strip().lower()))


def _run_ref(run_id: str) -> str | None:
    value = str(run_id or "").strip()
    if not value:
        return None
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


def _safe_stop_reason(reason: str) -> str | None:
    value = str(reason or "").strip().lower()
    if not value:
        return None
    if _SAFE_AUTO_STOP_REASON.fullmatch(value):
        return value
    return "operator_stop"


def _allowlist_summary(raw: str) -> tuple[int, list[str]]:
    errors: list[str] = []
    try:
        payload = json.loads(raw or "[]")
    except json.JSONDecodeError:
        return 0, ["allowlist_invalid_json"]
    if not isinstance(payload, list):
        return 0, ["allowlist_not_list"]
    normalized = [str(item).strip() for item in payload]
    if any(not item for item in normalized):
        errors.append("allowlist_empty_identifier")
    if len(normalized) != len(set(normalized)):
        errors.append("allowlist_duplicate_identifier")
    return len(normalized), errors


def _artifact_error_codes(errors: list[str]) -> list[str]:
    codes: list[str] = []
    for error in errors:
        value = str(error).lower()
        if "signing secret" in value:
            code = "signing_configuration_invalid"
        elif "configuration fingerprint" in value:
            code = "configuration_fingerprint_mismatch"
        elif "current release" in value:
            code = "current_release_invalid"
        elif "previous release" in value or "rollback pointer" in value:
            code = "previous_release_invalid"
        elif "runtime capability" in value or "pilot_runtime_guard capability" in value:
            code = "release_capability_invalid"
        elif "admission" in value:
            code = "admission_evidence_invalid"
        elif "pilot control" in value:
            code = "pilot_control_invalid"
        elif "evidence" in value:
            code = "evidence_file_invalid"
        else:
            code = "runtime_artifact_invalid"
        if code not in codes:
            codes.append(code)
    return codes


def _money_attention(db: Session, order_ids: list[int]) -> dict[str, int | bool]:
    if not order_ids:
        return {
            "payment_review_orders": 0,
            "refund_attention_orders": 0,
            "reconciliation_mismatches": 0,
            "attention_required": False,
        }

    payment_review_orders = (
        db.query(func.count(func.distinct(Order.id)))
        .filter(
            Order.id.in_(order_ids),
            or_(
                Order.status == "payment_review_required",
                Order.payment_status.in_(_PAYMENT_REVIEW_STATUSES),
            ),
        )
        .scalar()
        or 0
    )
    refund_attention_orders = (
        db.query(func.count(func.distinct(ReturnRequest.order_id)))
        .filter(
            ReturnRequest.order_id.in_(order_ids),
            ReturnRequest.status.in_(_REFUND_ATTENTION_STATUSES),
        )
        .scalar()
        or 0
    )
    reconciliation_mismatches = (
        db.query(func.count(PaymentReconciliation.id))
        .filter(
            PaymentReconciliation.order_id.in_(order_ids),
            PaymentReconciliation.status == "mismatch",
            PaymentReconciliation.resolved_at.is_(None),
        )
        .scalar()
        or 0
    )
    return {
        "payment_review_orders": int(payment_review_orders),
        "refund_attention_orders": int(refund_attention_orders),
        "reconciliation_mismatches": int(reconciliation_mismatches),
        "attention_required": bool(
            payment_review_orders or refund_attention_orders or reconciliation_mismatches
        ),
    }


def build_pilot_operations_status(
    db: Session,
    settings: "Settings",
    *,
    env: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    enforced = bool(settings.pilot_runtime_enforced)
    state_count = db.query(func.count(PilotRuntimeState.id)).scalar() or 0
    state = db.get(PilotRuntimeState, 1)

    if state is None:
        database_codes = ["runtime_state_missing"] if enforced else []
        return {
            "schema_version": 1,
            "generated_at": _generated_at(),
            "enforced": enforced,
            "checkout_decision": "NO-GO",
            "runtime": {
                "present": False,
                "status": "not_armed",
                "run_ref": None,
                "max_orders": 20,
                "accepted_orders": 0,
                "remaining_orders": 20,
                "slot_count": 0,
                "historical_slot_count": int(db.query(func.count(PilotOrderSlot.id)).scalar() or 0),
                "allowlist_count": 0,
                "stop_reason": None,
                "opened_at": None,
                "stopped_at": None,
                "completed_at": None,
                "updated_at": None,
            },
            "database_integrity": {
                "healthy": not database_codes,
                "codes": database_codes,
            },
            "artifact_integrity": {
                "applicable": False,
                "healthy": None,
                "codes": [],
            },
            "money_attention": _money_attention(db, []),
        }

    slots = (
        db.query(PilotOrderSlot)
        .filter(PilotOrderSlot.run_id == state.run_id)
        .order_by(PilotOrderSlot.sequence.asc())
        .all()
    )
    sequences = [int(slot.sequence) for slot in slots]
    order_ids = [int(slot.order_id) for slot in slots]
    historical_slot_count = (
        db.query(func.count(PilotOrderSlot.id))
        .filter(PilotOrderSlot.run_id != state.run_id)
        .scalar()
        or 0
    )
    allowlist_count, database_codes = _allowlist_summary(state.allowed_telegram_ids)

    if state_count != 1:
        database_codes.append("runtime_state_not_singleton")
    if state.max_orders != 20:
        database_codes.append("max_orders_not_twenty")
    if state.accepted_orders < 0 or state.accepted_orders > state.max_orders:
        database_codes.append("accepted_orders_out_of_range")
    if len(slots) != state.accepted_orders:
        database_codes.append("slot_count_mismatch")
    if sequences != list(range(1, state.accepted_orders + 1)):
        database_codes.append("slot_sequence_gap")
    if any(slot.admission_sha256 != state.admission_sha256 for slot in slots):
        database_codes.append("slot_admission_binding_mismatch")
    if len(set(order_ids)) != len(order_ids):
        database_codes.append("duplicate_pilot_order")
    if state.status in {"active", "stopped", "completed"} and not state.run_id:
        database_codes.append("run_id_missing")
    if state.status in {"active", "stopped", "completed"} and not _valid_sha256(
        state.admission_sha256
    ):
        database_codes.append("admission_binding_invalid")
    if state.status in {"active", "stopped", "completed"} and not _valid_sha256(
        state.release_sha256
    ):
        database_codes.append("release_binding_invalid")
    if state.status in {"active", "stopped", "completed"} and not state.pilot_state_created_at:
        database_codes.append("pilot_state_binding_missing")
    if state.status == "active" and allowlist_count == 0:
        database_codes.append("active_allowlist_empty")
    if state.status == "active" and state.accepted_orders >= state.max_orders:
        database_codes.append("active_runtime_exhausted")
    if state.status == "completed" and state.accepted_orders != state.max_orders:
        database_codes.append("completed_before_limit")
    if state.status == "stopped" and not str(state.stop_reason or "").strip():
        database_codes.append("stopped_without_reason")
    database_codes = list(dict.fromkeys(database_codes))

    artifact_applicable = bool(state.admission_sha256 and state.release_sha256)
    artifact_codes: list[str] = []
    artifact_healthy: bool | None = None
    if artifact_applicable:
        try:
            artifact_errors = validate_runtime_files(state, settings, env=env)
        except Exception:
            artifact_errors = ["runtime validator failed"]
        artifact_codes = _artifact_error_codes(artifact_errors)
        artifact_healthy = not artifact_codes

    money_attention = _money_attention(db, order_ids)
    remaining_orders = max(int(state.max_orders) - int(state.accepted_orders), 0)
    database_healthy = not database_codes
    integrity_healthy = database_healthy and artifact_healthy is True
    checkout_ready = bool(
        enforced
        and state.status == "active"
        and remaining_orders > 0
        and integrity_healthy
        and not money_attention["attention_required"]
    )

    return {
        "schema_version": 1,
        "generated_at": _generated_at(),
        "enforced": enforced,
        "checkout_decision": "GO" if checkout_ready else "NO-GO",
        "runtime": {
            "present": True,
            "status": state.status,
            "run_ref": _run_ref(state.run_id),
            "max_orders": int(state.max_orders),
            "accepted_orders": int(state.accepted_orders),
            "remaining_orders": remaining_orders,
            "slot_count": len(slots),
            "historical_slot_count": int(historical_slot_count),
            "allowlist_count": allowlist_count,
            "stop_reason": _safe_stop_reason(state.stop_reason),
            "opened_at": _timestamp(state.opened_at),
            "stopped_at": _timestamp(state.stopped_at),
            "completed_at": _timestamp(state.completed_at),
            "updated_at": _timestamp(state.updated_at),
        },
        "database_integrity": {
            "healthy": database_healthy,
            "codes": database_codes,
        },
        "artifact_integrity": {
            "applicable": artifact_applicable,
            "healthy": artifact_healthy,
            "codes": artifact_codes,
        },
        "money_attention": money_attention,
    }
