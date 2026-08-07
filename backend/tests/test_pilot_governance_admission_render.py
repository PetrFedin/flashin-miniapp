from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from pilot_governance_admission import _render_manifest  # noqa: E402


def test_final_admission_summary_keeps_lifecycle_and_governance_evidence():
    manifest = {
        "decision": "GO",
        "created_at": "2026-08-06T12:00:00Z",
        "expires_at": "2026-08-06T13:00:00Z",
        "release": {
            "release_id": "release-governance",
            "git_commit": "a" * 40,
        },
        "approvals": {
            "business_owner": "Business",
            "operations_owner": "Operations",
            "technical_owner": "Technical",
            "legal_owner": "Legal",
            "support_owner": "Support",
        },
        "evidence": {
            "live_lifecycle_report": {
                "path": "docs/pilot/live_lifecycle_report.json",
                "sha256": "b" * 64,
            },
            "repository_governance_report": {
                "path": "docs/pilot/repository_governance_report.json",
                "sha256": "c" * 64,
            },
        },
    }

    rendered = _render_manifest(manifest)

    assert "## Live lifecycle evidence" in rendered
    assert "docs/pilot/live_lifecycle_report.json" in rendered
    assert "b" * 64 in rendered
    assert "## Repository governance evidence" in rendered
    assert "docs/pilot/repository_governance_report.json" in rendered
    assert "c" * 64 in rendered
