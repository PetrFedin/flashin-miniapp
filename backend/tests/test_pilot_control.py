from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from pilot_control import SCENARIOS, new_state, record_scenario, validate_state  # noqa: E402

ADMISSION_BINDING = {
    "manifest_sha256": "a" * 64,
    "created_at": "2026-08-05T12:00:00Z",
    "configuration_fingerprint": "b" * 64,
    "release": {
        "release_id": "pilot-release",
        "git_commit": "c" * 40,
        "sha256": "d" * 64,
    },
}


def valid_changes(scenario):
    number = scenario["number"]
    changes = {
        "result": "pass",
        "evidence": [f"evidence-{number}"],
        "note": "verified",
    }
    if scenario.get("requires_order"):
        changes.update(
            order_id=f"order-{number}",
            order_status=scenario["expected_order_status"],
        )
    if scenario.get("requires_payment"):
        changes.update(
            payment_id=f"payment-{number}",
            payment_status="succeeded",
            expected_amount="1000.00",
            provider_amount="1000.00",
            currency="RUB",
            provider_currency="RUB",
        )
    if scenario.get("requires_refund"):
        changes.update(
            refund_id=f"refund-{number}",
            refund_status="succeeded",
        )
    if scenario.get("requires_stock"):
        expected_delta = scenario.get("expected_stock_delta", 1)
        changes.update(
            stock_before=10,
            stock_after=10 - expected_delta,
            expected_stock_delta=expected_delta,
        )
    if scenario.get("requires_webhook_idempotency"):
        changes.update(webhook_deliveries=2, domain_effects=1)
    return changes


def completed_state():
    state = new_state(ADMISSION_BINDING)
    for scenario in SCENARIOS:
        record_scenario(state, scenario["number"], **valid_changes(scenario))
    return state


def test_new_state_contains_20_scenarios_and_is_no_go():
    state = new_state(ADMISSION_BINDING)
    report = validate_state(state, final=False)
    assert len(state["scenarios"]) == 20
    assert report["decision"] == "NO-GO"
    assert report["summary"]["todo"] == 20


def test_final_validation_is_go_only_after_all_scenarios_pass():
    report = validate_state(completed_state(), final=True)
    assert report["decision"] == "GO"
    assert report["errors"] == []
    assert report["stop_reasons"] == []


def test_duplicate_payment_id_stops_pilot():
    state = completed_state()
    state["scenarios"][1]["payment_id"] = state["scenarios"][0]["payment_id"]
    report = validate_state(state, final=False)
    assert report["decision"] == "STOP"
    assert any("Duplicate payment identifier" in reason for reason in report["stop_reasons"])


def test_amount_or_currency_mismatch_stops_pilot():
    state = completed_state()
    state["scenarios"][0]["provider_amount"] = "999.00"
    state["scenarios"][1]["provider_currency"] = "USD"
    report = validate_state(state, final=False)
    assert report["decision"] == "STOP"
    assert any("payment amount mismatch" in reason for reason in report["stop_reasons"])
    assert any("payment currency mismatch" in reason for reason in report["stop_reasons"])


def test_negative_stock_or_wrong_delta_stops_pilot():
    state = completed_state()
    state["scenarios"][0]["stock_after"] = -1
    state["scenarios"][1]["expected_stock_delta"] = 2
    report = validate_state(state, final=False)
    assert report["decision"] == "STOP"
    assert any("stock_after is negative" in reason for reason in report["stop_reasons"])
    assert any("stock delta mismatch" in reason for reason in report["stop_reasons"])


def test_repeated_webhook_must_have_one_domain_effect():
    state = completed_state()
    scenario_index = next(
        index for index, scenario in enumerate(SCENARIOS) if scenario.get("requires_webhook_idempotency")
    )
    state["scenarios"][scenario_index]["domain_effects"] = 2
    report = validate_state(state, final=False)
    assert report["decision"] == "STOP"
    assert any("domain effects" in reason for reason in report["stop_reasons"])


def test_final_validation_rejects_incomplete_or_missing_evidence():
    state = completed_state()
    state["scenarios"][0]["result"] = "blocked"
    state["scenarios"][1]["evidence"] = []
    report = validate_state(state, final=True)
    assert report["decision"] == "NO-GO"
    assert any("20 passed scenarios" in error for error in report["errors"])
    assert any("missing evidence" in error for error in report["errors"])


def test_live_pilot_artifacts_are_gitignored():
    ignored = (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "docs/pilot/live_pilot_state.json" in ignored
    assert "docs/pilot/live_pilot_summary.md" in ignored
