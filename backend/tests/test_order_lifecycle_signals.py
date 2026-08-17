import json

from backend.services.order_lifecycle_signals import apply_operational_signals


def base_reconciliation(status="PASS"):
    return {
        "schema_version": 1,
        "overall_status": status,
        "requires_operator_action": status in {"REVIEW", "BLOCKED"},
        "stages": [],
    }


def test_unresolved_business_event_is_pending_not_operator_incident():
    result = apply_operational_signals(
        base_reconciliation("PASS"),
        {"attention": {"business_events_unresolved": 2, "business_events_failed": 0}},
    )

    assert result["overall_status"] == "PENDING"
    assert result["requires_operator_action"] is False
    assert result["operational_signals"] == [
        {
            "key": "business_events",
            "status": "PENDING",
            "reason": "business_event_processing_in_progress",
            "next_action": "wait_for_business_event_worker",
            "evidence": ["business_events.unresolved=2"],
        }
    ]


def test_failed_business_event_requires_review_without_becoming_seventh_stage():
    source = base_reconciliation("PENDING")
    source["stages"] = [{"key": "payment", "status": "PENDING"}]

    result = apply_operational_signals(
        source,
        {"attention": {"business_events_unresolved": 3, "business_events_failed": 1}},
    )

    assert result["overall_status"] == "REVIEW"
    assert result["requires_operator_action"] is True
    assert result["stages"] == [{"key": "payment", "status": "PENDING"}]
    assert result["operational_signals"][0]["key"] == "business_events"
    assert result["operational_signals"][0]["status"] == "REVIEW"


def test_existing_blocked_stage_cannot_be_downgraded_by_pending_signal():
    result = apply_operational_signals(
        base_reconciliation("BLOCKED"),
        {"attention": {"business_events_unresolved": 1, "business_events_failed": 0}},
    )

    assert result["overall_status"] == "BLOCKED"
    assert result["requires_operator_action"] is True


def test_operational_signal_output_never_reflects_event_payload_or_error_text():
    result = apply_operational_signals(
        base_reconciliation("PASS"),
        {
            "attention": {"business_events_unresolved": 1, "business_events_failed": 0},
            "business_events": [
                {
                    "status": "pending",
                    "payload_json": '{"secret":"never-emit"}',
                    "last_error": "provider-private-error",
                }
            ],
        },
    )
    encoded = json.dumps(result, sort_keys=True)

    assert "never-emit" not in encoded
    assert "provider-private-error" not in encoded
