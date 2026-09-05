from __future__ import annotations

from typing import Any, Mapping, TYPE_CHECKING

from sqlalchemy.orm import Session

from .diagnostics import run_diagnostics
from .pilot_observability import build_pilot_operations_status

if TYPE_CHECKING:
    from ..config import Settings

_CRITICAL_DIAGNOSTIC_CHECKS = (
    "database",
    "migrations",
    "env",
    "payments",
    "moysklad",
    "scheduler",
    "notification_delivery",
    "webhook_outbox",
    "moysklad_sync",
)
_ADVISORY_DIAGNOSTIC_CHECKS = ("media", "search")


def _diagnostic_status(snapshot: Mapping[str, Any], name: str) -> bool | None:
    checks = snapshot.get("checks")
    if not isinstance(checks, Mapping):
        return None
    check = checks.get(name)
    if not isinstance(check, Mapping):
        return None
    value = check.get("ok")
    return value if isinstance(value, bool) else None


def _runtime_summary(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    runtime = snapshot.get("runtime") if isinstance(snapshot.get("runtime"), Mapping) else {}
    database = snapshot.get("database_integrity") if isinstance(snapshot.get("database_integrity"), Mapping) else {}
    artifact = snapshot.get("artifact_integrity") if isinstance(snapshot.get("artifact_integrity"), Mapping) else {}
    continuation = snapshot.get("continuation") if isinstance(snapshot.get("continuation"), Mapping) else {}
    money = snapshot.get("money_attention") if isinstance(snapshot.get("money_attention"), Mapping) else {}
    operational = snapshot.get("operational_safety") if isinstance(snapshot.get("operational_safety"), Mapping) else {}
    return {
        "checkout_decision": str(snapshot.get("checkout_decision") or "NO-GO"),
        "enforced": bool(snapshot.get("enforced")),
        "status": runtime.get("status"),
        "accepted_orders": runtime.get("accepted_orders"),
        "remaining_orders": runtime.get("remaining_orders"),
        "allowlist_count": runtime.get("allowlist_count"),
        "database_integrity_healthy": database.get("healthy"),
        "artifact_integrity_applicable": artifact.get("applicable"),
        "artifact_integrity_healthy": artifact.get("healthy"),
        "continuation_applicable": continuation.get("applicable"),
        "continuation_ready": continuation.get("ready"),
        "next_sequence": continuation.get("next_sequence"),
        "money_attention_required": bool(money.get("attention_required")),
        "operational_safety_applicable": operational.get("applicable"),
        "operational_safety_healthy": operational.get("healthy"),
    }


def compose_pilot_readiness(
    diagnostics: Mapping[str, Any] | None,
    runtime_status: Mapping[str, Any] | None,
) -> dict[str, Any]:
    blocking_codes: list[str] = []
    warning_codes: list[str] = []

    diagnostics_available = isinstance(diagnostics, Mapping) and bool(diagnostics)
    runtime_available = isinstance(runtime_status, Mapping) and bool(runtime_status)
    diagnostics = diagnostics if diagnostics_available else {}
    runtime_status = runtime_status if runtime_available else {}

    if not diagnostics_available:
        blocking_codes.append("diagnostics_unavailable")
    else:
        for name in _CRITICAL_DIAGNOSTIC_CHECKS:
            status = _diagnostic_status(diagnostics, name)
            if status is None:
                blocking_codes.append(f"diagnostic_missing:{name}")
            elif status is False:
                blocking_codes.append(f"diagnostic_failed:{name}")
        for name in _ADVISORY_DIAGNOSTIC_CHECKS:
            status = _diagnostic_status(diagnostics, name)
            if status is None:
                warning_codes.append(f"diagnostic_missing:{name}")
            elif status is False:
                warning_codes.append(f"diagnostic_degraded:{name}")

    if not runtime_available:
        blocking_codes.append("runtime_status_unavailable")
    elif runtime_status.get("checkout_decision") != "GO":
        blocking_codes.append("runtime_checkout_no_go")

    runtime = _runtime_summary(runtime_status)
    if runtime_available:
        if runtime.get("database_integrity_healthy") is not True:
            blocking_codes.append("runtime_database_integrity_failed")
        if runtime.get("artifact_integrity_applicable") is not True:
            blocking_codes.append("runtime_artifact_integrity_unavailable")
        elif runtime.get("artifact_integrity_healthy") is not True:
            blocking_codes.append("runtime_artifact_integrity_failed")
        if runtime.get("continuation_applicable") is True and runtime.get("continuation_ready") is False:
            blocking_codes.append("runtime_previous_scenario_pending")
        if runtime.get("money_attention_required"):
            blocking_codes.append("runtime_money_attention")
        if runtime.get("operational_safety_applicable") is not True:
            blocking_codes.append("runtime_operational_safety_unavailable")
        elif runtime.get("operational_safety_healthy") is not True:
            blocking_codes.append("runtime_operational_safety_failed")

    blocking_codes = sorted(set(blocking_codes))
    warning_codes = sorted(set(warning_codes))
    ready = not blocking_codes and runtime.get("checkout_decision") == "GO"

    critical_checks = {
        name: _diagnostic_status(diagnostics, name) if diagnostics_available else None
        for name in _CRITICAL_DIAGNOSTIC_CHECKS
    }
    advisory_checks = {
        name: _diagnostic_status(diagnostics, name) if diagnostics_available else None
        for name in _ADVISORY_DIAGNOSTIC_CHECKS
    }

    return {
        "schema_version": 1,
        "decision": "GO" if ready else "NO-GO",
        "ready_for_next_order": ready,
        "blocking_codes": blocking_codes,
        "warning_codes": warning_codes,
        "diagnostics": {
            "critical": critical_checks,
            "advisory": advisory_checks,
        },
        "runtime": runtime,
    }


def build_pilot_readiness(
    db: Session,
    settings: "Settings",
    *,
    env: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    diagnostics: Mapping[str, Any] | None
    runtime_status: Mapping[str, Any] | None

    try:
        diagnostics = run_diagnostics(db)
    except Exception:
        diagnostics = None

    try:
        runtime_status = build_pilot_operations_status(db, settings, env=env)
    except Exception:
        runtime_status = None

    return compose_pilot_readiness(diagnostics, runtime_status)


__all__ = ["build_pilot_readiness", "compose_pilot_readiness"]