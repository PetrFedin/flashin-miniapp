from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from pilot_governance_workflow_policy import (  # noqa: E402
    validate_protected_branch_workflow,
)


def _report(*, event="push", path=".github/workflows/ci.yml", workflow_sha=None):
    branch_sha = "a" * 40
    return {
        "branch": {"name": "main", "head_sha": branch_sha},
        "workflow": {
            "id": 677,
            "event": event,
            "path": path,
            "head_sha": workflow_sha or branch_sha,
            "status": "completed",
            "conclusion": "success",
        },
    }


def test_protected_branch_push_workflow_is_required():
    assert validate_protected_branch_workflow(
        _report(),
        workflow_path="ci.yml",
    ) == []

    errors = validate_protected_branch_workflow(
        _report(event="pull_request"),
        workflow_path="ci.yml",
    )
    assert "repository governance workflow must be a push run on the protected branch" in errors


def test_exact_workflow_path_and_branch_head_are_required():
    errors = validate_protected_branch_workflow(
        _report(path=".github/workflows/other.yml", workflow_sha="b" * 40),
        workflow_path="ci.yml",
    )
    assert "repository governance workflow path is not the exact tracked workflow" in errors
    assert "repository governance workflow is not for the protected branch head" in errors
