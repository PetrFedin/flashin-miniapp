import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from real_e2e_context_status import inspect_context  # noqa: E402


def _write_context(path: Path, *, phase: str, **overrides) -> None:
    payload = {
        "schema_version": 1,
        "kind": "flashin_real_order_e2e_context",
        "phase": phase,
        "api_base": "https://api.flashin.example",
        "variant_id": 9,
        "baseline_stock_qty": 5,
        "baseline_reserved_qty": 0,
        "provider": "yookassa",
    }
    payload.update(overrides)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_checkout_intent_is_fail_closed_and_requires_investigation(tmp_path):
    path = tmp_path / "context.json"
    _write_context(path, phase="checkout_intent")

    report = inspect_context(path)

    assert not report["ok"]
    assert report["phase"] == "checkout_intent"
    assert report["requires_investigation"] is True
    assert "Do not rerun real-order E2E" in report["recovery_action"]


def test_order_created_is_fail_closed_without_creating_another_payment(tmp_path):
    path = tmp_path / "context.json"
    _write_context(
        path,
        phase="order_created",
        order_id=42,
        subject_id="order:42",
    )

    report = inspect_context(path)

    assert not report["ok"]
    assert report["order_id"] == 42
    assert report["requires_investigation"] is True
    assert "authoritative YooKassa" in report["recovery_action"]


def test_payment_created_is_ready_for_same_order_lifecycle(tmp_path):
    path = tmp_path / "context.json"
    _write_context(
        path,
        phase="payment_created",
        order_id=42,
        subject_id="order:42",
        provider_payment_id="payment-42",
    )

    report = inspect_context(path)

    assert report["ok"] is True
    assert report["requires_investigation"] is False
    assert report["provider_payment_id"] == "payment-42"
    assert report["errors"] == []


def test_invalid_or_incomplete_context_never_returns_go(tmp_path):
    missing = inspect_context(tmp_path / "missing.json")
    assert not missing["ok"]

    path = tmp_path / "context.json"
    _write_context(path, phase="payment_created", order_id=42, subject_id="order:42")
    incomplete = inspect_context(path)
    assert not incomplete["ok"]
    assert "provider_payment_id is missing" in " ".join(incomplete["errors"])
