import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from pilot_lifecycle_admission import (  # noqa: E402
    ORDER_CONTEXT_EVIDENCE_PATH,
    validate_order_lifecycle_correlation,
)


def _write_context(root: Path, *, api_base: str) -> None:
    context_path = root / ORDER_CONTEXT_EVIDENCE_PATH
    context_path.parent.mkdir(parents=True, exist_ok=True)
    context_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "kind": "flashin_real_order_e2e_context",
                "api_base": api_base,
                "provider": "yookassa",
                "subject_id": "order:42",
                "order_id": 42,
                "baseline_reserved_qty": 0,
            }
        ),
        encoding="utf-8",
    )


def _report() -> dict:
    return {
        "scenarios": [
            {
                "name": "yookassa_payment_redirect",
                "subject_id": "order:42",
                "evidence": [
                    {
                        "path": ORDER_CONTEXT_EVIDENCE_PATH,
                        "sha256": "a" * 64,
                    }
                ],
            }
        ]
    }


def test_real_order_context_must_match_pilot_api_base(tmp_path):
    _write_context(tmp_path, api_base="https://staging.flashin.example/")

    errors = validate_order_lifecycle_correlation(
        _report(),
        root=tmp_path,
        expected_api_base="https://api.flashin.example",
    )

    assert "real-order E2E context api_base does not match pilot API_PUBLIC_URL" in errors


def test_real_order_context_accepts_normalized_pilot_api_base(tmp_path):
    _write_context(tmp_path, api_base="https://api.flashin.example/")

    errors = validate_order_lifecycle_correlation(
        _report(),
        root=tmp_path,
        expected_api_base="https://api.flashin.example",
    )

    assert errors == []
