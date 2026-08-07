from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from check_integrations import (  # noqa: E402
    PROBES,
    build_report,
    evidence_safe_result,
    status_only_report_errors,
    verify_existing_report,
)
from pilot_evidence import sign_payload  # noqa: E402


def _env() -> dict[str, str]:
    return {
        "PILOT_EVIDENCE_SIGNING_SECRET": "status-only-evidence-secret-0123456789abcdef",
        "APP_ENV": "production",
        "TELEGRAM_BOT_TOKEN": "telegram-secret",
        "YOOKASSA_SHOP_ID": "shop-1",
        "YOOKASSA_SECRET_KEY": "yookassa-secret",
        "YOOKASSA_RETURN_URL": "https://mini.flashin.test/payment-result",
        "MOYSKLAD_TOKEN": "moysklad-secret",
        "MEDIA_STORAGE": "r2",
        "S3_ACCESS_KEY_ID": "access-key",
        "S3_SECRET_ACCESS_KEY": "storage-secret",
        "MEILISEARCH_ENABLED": "true",
        "MEILISEARCH_MASTER_KEY": "search-secret",
    }


def _release() -> dict[str, str]:
    return {
        "release_id": "release-v27",
        "git_commit": "a" * 40,
        "sha256": "b" * 64,
        "promoted_at": "2026-08-08T00:00:00Z",
    }


def _passing_results() -> list[dict[str, object]]:
    return [
        {
            "name": probe.name,
            "ok": True,
            "returncode": 0,
            "stdout": f"provider-private-output-{probe.name}@example.test",
            "stderr": f"https://provider.invalid/{probe.name}/private-body",
        }
        for probe in PROBES
    ]


def _signed_report() -> dict[str, object]:
    return build_report(
        _passing_results(),
        strict=True,
        host_python=False,
        env=_env(),
        current_release=_release(),
        max_age_minutes=60,
        run_id="status-only-run",
        created_at=datetime.now(UTC),
    )


def test_signed_provider_report_discards_all_probe_stdout_and_stderr():
    report = _signed_report()
    serialized = json.dumps(report, ensure_ascii=False)

    assert "provider-private-output" not in serialized
    assert "provider.invalid" not in serialized
    assert report["go"] is True
    assert report["summary"] == {"total": 5, "passed": 5, "failed": 0}
    for result in report["results"]:
        assert result["stdout"] == ""
        assert result["stderr"] == ""
        assert set(result) == {"name", "ok", "returncode", "stdout", "stderr"}


def test_evidence_result_bounds_unknown_names_and_return_codes():
    safe = evidence_safe_result(
        {
            "name": "customer-private-identifier",
            "ok": True,
            "returncode": "private-provider-reference",
            "stdout": "secret-output",
            "stderr": "secret-error",
            "provider_body": "secret-body",
        }
    )
    assert safe == {
        "name": "unknown",
        "ok": True,
        "returncode": None,
        "stdout": "",
        "stderr": "",
    }


def test_verifier_rejects_resigned_report_that_reintroduces_probe_output(tmp_path: Path):
    env = _env()
    report = _signed_report()
    tampered = dict(report)
    tampered.pop("signature", None)
    tampered_results = [dict(item) for item in report["results"]]
    tampered_results[0]["stdout"] = "provider-private-body-not-known-to-redaction"
    tampered_results[0]["provider_body"] = "another-private-provider-value"
    tampered["results"] = tampered_results
    resigned = sign_payload(tampered, env["PILOT_EVIDENCE_SIGNING_SECRET"])

    report_path = tmp_path / "provider-report.json"
    report_path.write_text(json.dumps(resigned), encoding="utf-8")
    loaded, errors = verify_existing_report(
        report_path,
        env=env,
        current_release=_release(),
        max_age_minutes=60,
    )

    assert loaded is not None
    assert "provider evidence must not retain probe stdout/stderr" in errors
    assert "provider evidence result contains unsupported fields" in errors


def test_status_only_policy_accepts_generated_report():
    report = _signed_report()
    assert status_only_report_errors(report) == []
