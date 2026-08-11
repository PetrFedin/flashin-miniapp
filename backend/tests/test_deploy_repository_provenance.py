from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from deploy_release_gate import validate_repository_provenance  # noqa: E402
from release_ci_gate import REQUIRED_CI_JOBS  # noqa: E402


SHA = "a" * 40


def _branch(*, protected=True, sha=SHA):
    return {
        "name": "main",
        "protected": protected,
        "commit": {"sha": sha},
    }


def _run(*, event="push", sha=SHA, conclusion="success"):
    return {
        "id": 4242,
        "name": "CI",
        "path": ".github/workflows/ci.yml",
        "event": event,
        "head_branch": "main",
        "head_sha": sha,
        "status": "completed",
        "conclusion": conclusion,
        "run_number": 99,
    }


def _jobs(*, failing=None):
    return {
        "jobs": [
            {
                "name": name,
                "status": "completed",
                "conclusion": "failure" if name == failing else "success",
            }
            for name in sorted(REQUIRED_CI_JOBS)
        ]
    }


def test_protected_exact_green_main_is_accepted_for_deploy():
    errors, run = validate_repository_provenance(
        expected_sha=SHA,
        branch_payload=_branch(),
        runs_payload={"workflow_runs": [_run()]},
        jobs_payload=_jobs(),
    )

    assert errors == []
    assert run is not None
    assert run["id"] == 4242


def test_unprotected_main_is_rejected_for_deploy():
    errors, _run_match = validate_repository_provenance(
        expected_sha=SHA,
        branch_payload=_branch(protected=False),
        runs_payload={"workflow_runs": [_run()]},
        jobs_payload=_jobs(),
    )

    assert "main is not protected" in errors


def test_release_commit_must_still_be_current_main_head():
    errors, _run_match = validate_repository_provenance(
        expected_sha=SHA,
        branch_payload=_branch(sha="b" * 40),
        runs_payload={"workflow_runs": [_run()]},
        jobs_payload=_jobs(),
    )

    assert "Release SHA is not the current main head" in errors


def test_pull_request_ci_cannot_authorize_production_deploy():
    errors, _run_match = validate_repository_provenance(
        expected_sha=SHA,
        branch_payload=_branch(),
        runs_payload={"workflow_runs": [_run(event="pull_request")]},
        jobs_payload=_jobs(),
    )

    assert "No successful exact-SHA push CI run exists for main" in errors


def test_any_required_job_failure_blocks_production_deploy():
    errors, _run_match = validate_repository_provenance(
        expected_sha=SHA,
        branch_payload=_branch(),
        runs_payload={"workflow_runs": [_run()]},
        jobs_payload=_jobs(failing="docker"),
    )

    assert "Required CI job docker is not successful" in errors
