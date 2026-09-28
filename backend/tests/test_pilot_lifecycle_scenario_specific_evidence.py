from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from pilot_lifecycle_admission import (  # noqa: E402
    ORDER_CONTEXT_EVIDENCE_PATH,
    validate_order_lifecycle_correlation,
)


def _scenario(evidence):
    return {
        "name": "yookassa_refund",
        "subject_id": "order:42",
        "evidence": evidence,
    }


def _context_entry():
    return {
        "label": "shared real-order E2E context",
        "path": ORDER_CONTEXT_EVIDENCE_PATH,
        "sha256": "a" * 64,
    }


def test_order_linked_scenario_cannot_use_shared_context_as_its_only_evidence():
    report = {"scenarios": [_scenario([_context_entry()])]}

    errors = validate_order_lifecycle_correlation(report)

    assert any(
        "must include scenario-specific evidence" in error
        for error in errors
    )


def test_order_linked_scenario_accepts_shared_context_plus_specific_evidence():
    report = {
        "scenarios": [
            _scenario(
                [
                    _context_entry(),
                    {
                        "label": "sanitized YooKassa refund observation",
                        "path": "docs/pilot/evidence/yookassa-refund-sanitized.json",
                        "sha256": "b" * 64,
                    },
                ]
            )
        ]
    }

    assert validate_order_lifecycle_correlation(report) == []
