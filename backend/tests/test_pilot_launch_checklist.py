import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from pilot_control_binding import LAUNCH_CHECKLIST_KEY, build_admission_binding  # noqa: E402
from pilot_evidence import configuration_fingerprint, sha256_file, sign_payload  # noqa: E402
from pilot_launch_admission import validate_attached_launch_checklist  # noqa: E402
from pilot_launch_checklist import (  # noqa: E402
    STEP_CONTRACT,
    build_report,
    normalize_input_steps,
    validate_report,
)

NOW = datetime(2026, 8, 7, 12, 0, tzinfo=UTC)
SECRET = "pilot-launch-checklist-secret-0123456789abcdef"
RELEASE = {
    "release_id": "release-launch-checklist",
    "git_commit": "a" * 40,
    "sha256": "b" * 64,
    "promoted_at": "2026-08-07T11:00:00Z",
}
APPROVALS = {
    "business_owner": "Business",
    "operations_owner": "Operations",
    "technical_owner": "Technical",
    "legal_owner": "Legal",
    "support_owner": "Support",
}


def _env():
    return {
        "APP_ENV": "production",
        "API_PUBLIC_URL": "https://api.flashin.example",
        "MINI_APP_URL": "https://mini.flashin.example",
        "ADMIN_URL": "https://admin.flashin.example",
        "PILOT_EVIDENCE_SIGNING_SECRET": SECRET,
        "PILOT_LAUNCH_CHECKLIST_MAX_AGE_HOURS": "24",
    }


def _prepare_root(tmp_path: Path):
    pilot_dir = tmp_path / "docs/pilot"
    evidence_dir = pilot_dir / "evidence"
    evidence_dir.mkdir(parents=True)
    evidence_path = evidence_dir / "launch-proof.txt"
    evidence_path.write_text("verified deployed pilot observation\n", encoding="utf-8")
    (tmp_path / ".env").write_text(
        "\n".join(f"{key}={value}" for key, value in _env().items()) + "\n",
        encoding="utf-8",
    )
    return pilot_dir, evidence_path


def _payload(evidence_path: Path, *, optional_skip=True):
    steps = []
    for step_id, title, critical in STEP_CONTRACT:
        if not critical and optional_skip:
            steps.append(
                {
                    "id": step_id,
                    "title": title,
                    "critical": critical,
                    "status": "skip",
                    "observed_at": "2026-08-07T11:55:00Z",
                    "owner": "Operations",
                    "comment": "Not enabled for this controlled pilot path",
                    "evidence": [],
                }
            )
        else:
            steps.append(
                {
                    "id": step_id,
                    "title": title,
                    "critical": critical,
                    "status": "pass",
                    "observed_at": "2026-08-07T11:55:00Z",
                    "owner": "Operations",
                    "comment": "Observed on deployed pilot stack",
                    "evidence": [
                        {
                            "label": "sanitized operator evidence",
                            "path": str(evidence_path),
                        }
                    ],
                }
            )
    return {"created_at": "2026-08-07T11:50:00Z", "steps": steps}


def _build_valid_report(tmp_path: Path):
    pilot_dir, evidence_path = _prepare_root(tmp_path)
    source_path = pilot_dir / "live_pilot_runner.json"
    payload = _payload(evidence_path)
    source_path.write_text(json.dumps(payload), encoding="utf-8")
    report = build_report(
        payload,
        source_path=source_path,
        root=tmp_path,
        env=_env(),
        current_release=RELEASE,
        max_age_hours=24,
        now=NOW,
    )
    return pilot_dir, source_path, report


def test_todo_or_skipped_critical_steps_fail_closed(tmp_path):
    _pilot_dir, evidence_path = _prepare_root(tmp_path)
    payload = _payload(evidence_path)
    payload["steps"][0]["status"] = "todo"
    payload["steps"][0]["evidence"] = []

    _steps, errors = normalize_input_steps(
        payload,
        root=tmp_path,
        env=_env(),
        now=NOW,
        maximum_age=timedelta(hours=24),
    )
    assert "P01 is critical and must be pass" in errors

    payload = _payload(evidence_path)
    payload["steps"][0]["status"] = "skip"
    payload["steps"][0]["comment"] = "skip it"
    payload["steps"][0]["evidence"] = []
    _steps, errors = normalize_input_steps(
        payload,
        root=tmp_path,
        env=_env(),
        now=NOW,
        maximum_age=timedelta(hours=24),
    )
    assert "P01 is critical and must be pass" in errors
    assert "P01 critical step cannot be skipped" in errors


def test_optional_skip_requires_named_operator_timestamp_and_reason(tmp_path):
    _pilot_dir, evidence_path = _prepare_root(tmp_path)
    payload = _payload(evidence_path)
    p05 = payload["steps"][4]
    p05["owner"] = ""
    p05["observed_at"] = ""
    p05["comment"] = "short"
    _steps, errors = normalize_input_steps(
        payload,
        root=tmp_path,
        env=_env(),
        now=NOW,
        maximum_age=timedelta(hours=24),
    )
    assert "P05 owner is missing" in errors
    assert any(error.startswith("P05: observed_at") for error in errors)
    assert "P05 skip requires a meaningful comment" in errors


def test_signed_report_binds_source_release_config_and_evidence(tmp_path):
    _pilot_dir, source_path, report = _build_valid_report(tmp_path)
    assert validate_report(
        report,
        root=tmp_path,
        env=_env(),
        expected_release=RELEASE,
        max_age_hours=24,
        now=NOW,
    ) == []

    tampered = json.loads(json.dumps(report))
    tampered["steps"][0]["comment"] = "tampered"
    errors = validate_report(
        tampered,
        root=tmp_path,
        env=_env(),
        expected_release=RELEASE,
        max_age_hours=24,
        now=NOW,
    )
    assert "launch checklist evidence signature is invalid" in errors

    source_path.write_text(source_path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    errors = validate_report(
        report,
        root=tmp_path,
        env=_env(),
        expected_release=RELEASE,
        max_age_hours=24,
        now=NOW,
    )
    assert "launch checklist source checksum does not match" in errors


def test_raw_telegram_init_data_is_rejected_from_evidence(tmp_path):
    _pilot_dir, evidence_path = _prepare_root(tmp_path)
    evidence_path.write_text(
        "auth_date=1&query_id=q&user=%7B%7D&hash=deadbeef\n",
        encoding="utf-8",
    )
    payload = _payload(evidence_path)
    _steps, errors = normalize_input_steps(
        payload,
        root=tmp_path,
        env=_env(),
        now=NOW,
        maximum_age=timedelta(hours=24),
    )
    assert any("appears to contain raw Telegram init data" in error for error in errors)


def test_signed_governance_admission_cannot_bind_runtime_without_checklist(tmp_path):
    pilot_dir, _source_path, report = _build_valid_report(tmp_path)
    report_path = pilot_dir / "launch_checklist_report.json"
    report_path.write_text(json.dumps(report), encoding="utf-8")

    manifest = {
        "schema_version": 1,
        "kind": "pilot_admission",
        "decision": "GO",
        "created_at": "2026-08-07T11:45:00Z",
        "configuration_fingerprint": configuration_fingerprint(_env(), SECRET),
        "release": RELEASE,
        "pilot_contract": {
            "maximum_orders": 20,
            "automatic_stop_on_critical_failure": True,
            "mass_admission_forbidden": True,
        },
        "approvals": APPROVALS,
        "acknowledgements": {
            "live_lifecycle_completed": True,
            "repository_governance_verified": True,
        },
        "evidence": {
            "live_lifecycle_report": {"path": "fixture", "sha256": "c" * 64},
            "repository_governance_report": {"path": "fixture", "sha256": "d" * 64},
        },
    }
    manifest = sign_payload(manifest, SECRET)
    manifest_path = pilot_dir / "pilot_admission_manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ValueError, match="launch checklist evidence is invalid"):
        build_admission_binding(
            manifest_path,
            manifest,
            root=tmp_path,
            require_live_lifecycle=False,
            require_repository_governance=False,
        )

    unsigned = dict(manifest)
    unsigned.pop("signature", None)
    unsigned["acknowledgements"] = {
        **unsigned["acknowledgements"],
        "launch_checklist_completed": True,
    }
    unsigned["pilot_contract"] = {
        **unsigned["pilot_contract"],
        "launch_checklist_required": True,
    }
    unsigned["evidence"] = {
        **unsigned["evidence"],
        "launch_checklist_report": {
            "path": "docs/pilot/launch_checklist_report.json",
            "sha256": sha256_file(report_path),
        },
    }
    final_manifest = sign_payload(unsigned, SECRET)
    manifest_path.write_text(json.dumps(final_manifest), encoding="utf-8")

    assert validate_attached_launch_checklist(
        manifest_path,
        final_manifest,
        env=_env(),
        root=tmp_path,
        max_age_hours=24,
        now=NOW,
    ) == []
    binding = build_admission_binding(
        manifest_path,
        final_manifest,
        root=tmp_path,
        require_live_lifecycle=False,
        require_repository_governance=False,
    )
    assert binding[LAUNCH_CHECKLIST_KEY] == sha256_file(report_path)
