import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from release_ci_gate import REQUIRED_CI_JOBS, validate_release_context

SHA = "a" * 40
WORKFLOW = ROOT / ".github" / "workflows" / "release.yml"


def _branch(*, protected: bool = True, sha: str = SHA) -> dict:
    return {"name": "main", "protected": protected, "commit": {"sha": sha}}


def _runs(*, event: str = "push", conclusion: str = "success", sha: str = SHA) -> dict:
    return {
        "workflow_runs": [
            {
                "id": 123,
                "run_number": 9,
                "name": "CI",
                "path": ".github/workflows/ci.yml",
                "event": event,
                "head_branch": "main",
                "head_sha": sha,
                "status": "completed",
                "conclusion": conclusion,
            }
        ]
    }


def _jobs(*, omit: str | None = None, fail: str | None = None) -> dict:
    return {
        "jobs": [
            {
                "name": name,
                "status": "completed",
                "conclusion": "failure" if name == fail else "success",
            }
            for name in sorted(REQUIRED_CI_JOBS)
            if name != omit
        ]
    }


def _errors(**overrides) -> list[str]:
    errors, _run = validate_release_context(
        ref_name=overrides.get("ref_name", "main"),
        expected_sha=overrides.get("expected_sha", SHA),
        branch_payload=overrides.get("branch_payload", _branch()),
        runs_payload=overrides.get("runs_payload", _runs()),
        jobs_payload=overrides.get("jobs_payload", _jobs()),
    )
    return errors


def test_exact_protected_main_with_all_six_push_ci_jobs_passes():
    assert _errors() == []


def test_release_gate_rejects_unprotected_nonmain_or_moved_main():
    assert "main is not protected" in _errors(branch_payload=_branch(protected=False))
    assert "Release artifacts may only be created from main" in _errors(ref_name="feature")
    assert "Release SHA is not the current main head" in _errors(
        branch_payload=_branch(sha="b" * 40)
    )


def test_pull_request_ci_never_substitutes_for_exact_push_ci():
    errors = _errors(runs_payload=_runs(event="pull_request"), jobs_payload=None)
    assert "No successful exact-SHA push CI run exists for main" in errors


def test_release_gate_requires_each_exact_ci_job_success():
    missing = _errors(jobs_payload=_jobs(omit="docker"))
    assert any("missing required jobs: docker" in error for error in missing)

    failed = _errors(jobs_payload=_jobs(fail="integrated-e2e"))
    assert "Required CI job integrated-e2e is not successful" in failed


def test_release_workflow_cannot_publish_raw_or_unverified_workspace_zip():
    source = WORKFLOW.read_text(encoding="utf-8")

    assert "actions: read" in source
    assert "python3 scripts/release_ci_gate.py" in source
    assert "scripts/release_control.py create" in source
    assert "scripts/release_control.py verify" in source
    assert "scripts/pilot_release_capability.py inspect" in source
    assert re.search(r"actions/upload-artifact@[0-9a-f]{40}", source)
    assert "actions/upload-artifact@v4" not in source
    assert "if-no-files-found: error" in source
    assert "zip -r" not in source
    assert "flashin-miniapp-release.zip" not in source
