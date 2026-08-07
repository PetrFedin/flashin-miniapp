from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from pilot_journey_binding import (  # noqa: E402
    admission_reference_errors,
    anchor_membership_errors,
    build_anchor,
    normalize_journey_id,
    validate_anchor,
)


NOW = datetime(2026, 8, 8, 12, 0, tzinfo=UTC)
SECRET = "pilot-journey-binding-secret-0123456789abcdef"
JOURNEY_ID = "4af679a8-df68-4dd6-906b-f85ebca61b4b"
RELEASE = {
    "release_id": "release-v29",
    "git_commit": "a" * 40,
    "sha256": "b" * 64,
    "promoted_at": "2026-08-08T11:00:00Z",
}


def _env():
    return {
        "PILOT_EVIDENCE_SIGNING_SECRET": SECRET,
        "APP_ENV": "production",
        "API_PUBLIC_URL": "https://api.flashin.example",
        "MINI_APP_URL": "https://mini.flashin.example",
        "ADMIN_URL": "https://admin.flashin.example",
    }


def _evidence_report(path: str, digest: str, *, lifecycle: bool):
    key = "scenarios" if lifecycle else "steps"
    return {
        key: [
            {
                "evidence": [
                    {
                        "label": "controlled journey anchor",
                        "path": path,
                        "sha256": digest,
                    }
                ]
            }
        ]
    }


def test_anchor_is_release_config_signed_and_uuid4_bound():
    anchor = build_anchor(
        env=_env(),
        current_release=RELEASE,
        max_age_hours=24,
        now=NOW,
        journey_id=JOURNEY_ID,
    )

    assert anchor["journey_id"] == JOURNEY_ID
    assert validate_anchor(
        anchor,
        env=_env(),
        current_release=RELEASE,
        max_age_hours=24,
        now=NOW,
    ) == []

    changed = dict(_env())
    changed["API_PUBLIC_URL"] = "https://other.flashin.example"
    errors = validate_anchor(
        anchor,
        env=changed,
        current_release=RELEASE,
        max_age_hours=24,
        now=NOW,
    )
    assert any("configuration fingerprint" in error for error in errors)


def test_journey_id_requires_canonical_non_nil_uuid4():
    assert normalize_journey_id(JOURNEY_ID) == JOURNEY_ID
    for invalid in (
        "",
        "00000000-0000-0000-0000-000000000000",
        "6ba7b810-9dad-11d1-80b4-00c04fd430c8",
        "{4af679a8-df68-4dd6-906b-f85ebca61b4b}",
        "customer@example.test",
    ):
        with pytest.raises(ValueError, match="canonical UUIDv4"):
            normalize_journey_id(invalid)


def test_lifecycle_and_checklist_must_reference_exact_same_anchor():
    path = "docs/pilot/evidence/controlled_journey_anchor.json"
    digest = "c" * 64
    lifecycle = _evidence_report(path, digest, lifecycle=True)
    checklist = _evidence_report(path, digest, lifecycle=False)

    assert anchor_membership_errors(
        lifecycle,
        checklist,
        anchor_path=path,
        anchor_sha256=digest,
    ) == []

    mismatched_checklist = _evidence_report(path, "d" * 64, lifecycle=False)
    errors = anchor_membership_errors(
        lifecycle,
        mismatched_checklist,
        anchor_path=path,
        anchor_sha256=digest,
    )
    assert "P01-P20 checklist does not reference the controlled journey anchor" in errors


def test_final_admission_must_reference_exact_bound_reports():
    lifecycle_sha = "e" * 64
    checklist_sha = "f" * 64
    manifest = {
        "evidence": {
            "live_lifecycle_report": {"sha256": lifecycle_sha},
            "launch_checklist_report": {"sha256": checklist_sha},
        }
    }
    assert admission_reference_errors(
        manifest,
        lifecycle_sha256=lifecycle_sha,
        checklist_sha256=checklist_sha,
    ) == []

    manifest["evidence"]["launch_checklist_report"]["sha256"] = "0" * 64
    errors = admission_reference_errors(
        manifest,
        lifecycle_sha256=lifecycle_sha,
        checklist_sha256=checklist_sha,
    )
    assert "final pilot admission launch_checklist_report checksum does not match bound evidence" in errors


def test_pilot_runner_requires_journey_binding_after_final_admission():
    source = (SCRIPTS / "pilot_runner.py").read_text(encoding="utf-8")
    assert "from pilot_journey_binding import verify_journey_binding" in source
    assert "errors = verify_final_admission(root=ROOT)" in source
    assert "errors = verify_journey_binding(ROOT)" in source
    assert source.index("verify_final_admission(root=ROOT)") < source.index("verify_journey_binding(ROOT)")
