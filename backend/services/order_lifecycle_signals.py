from __future__ import annotations

from copy import deepcopy
from typing import Any


_STATUS_RANK = {"PASS": 0, "PENDING": 1, "REVIEW": 2, "BLOCKED": 3}


def _count(attention: dict[str, Any], key: str) -> int:
    try:
        return max(0, int(attention.get(key) or 0))
    except (TypeError, ValueError):
        return 0


def _stricter(left: str, right: str) -> str:
    left_status = left if left in _STATUS_RANK else "REVIEW"
    right_status = right if right in _STATUS_RANK else "REVIEW"
    return left_status if _STATUS_RANK[left_status] >= _STATUS_RANK[right_status] else right_status


def apply_operational_signals(
    reconciliation: dict[str, Any],
    trace: dict[str, Any],
) -> dict[str, Any]:
    """Overlay cross-cutting sanitized worker signals onto six lifecycle stages.

    BusinessEvent recovery spans payment, inventory, provider, fulfillment and
    notification stages, so it remains an explicit operational signal instead
    of becoming a misleading seventh lifecycle stage. Only bounded counters
    from the sanitized trace are consumed; raw event payload/error data never
    reaches the reconciliation response.
    """

    result = deepcopy(reconciliation)
    attention = trace.get("attention") if isinstance(trace.get("attention"), dict) else {}
    failed = _count(attention, "business_events_failed")
    unresolved = _count(attention, "business_events_unresolved")
    signals: list[dict[str, Any]] = []

    if failed > 0:
        signals.append(
            {
                "key": "business_events",
                "status": "REVIEW",
                "reason": "business_event_recovery_required",
                "next_action": "inspect_business_event_recovery",
                "evidence": [f"business_events.failed={failed}"],
            }
        )
    elif unresolved > 0:
        signals.append(
            {
                "key": "business_events",
                "status": "PENDING",
                "reason": "business_event_processing_in_progress",
                "next_action": "wait_for_business_event_worker",
                "evidence": [f"business_events.unresolved={unresolved}"],
            }
        )

    overall = str(result.get("overall_status") or "REVIEW")
    for signal in signals:
        overall = _stricter(overall, str(signal["status"]))
    result["overall_status"] = overall
    result["requires_operator_action"] = bool(
        result.get("requires_operator_action") or overall in {"REVIEW", "BLOCKED"}
    )
    result["operational_signals"] = signals
    return result
