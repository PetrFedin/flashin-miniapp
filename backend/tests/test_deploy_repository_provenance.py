from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import deploy_release_gate as deploy_gate  # noqa: E402
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


def test_repository_trust_anchor_cannot_be_redirected():
    report = deploy_gate.verify_deploy_repository_provenance(
        SHA,
        repository="attacker/example",
    )

    assert not report["ok"]
    assert any("must be PetrFedin/flashin-miniapp" in error for error in report["errors"])


def test_github_api_trust_anchor_cannot_be_redirected():
    report = deploy_gate.verify_deploy_repository_provenance(
        SHA,
        api_base="https://github-api.attacker.example",
    )

    assert not report["ok"]
    assert any("must be https://api.github.com" in error for error in report["errors"])


def test_cli_cannot_print_deploy_path_before_repository_provenance_passes(
    monkeypatch,
    tmp_path,
    capsys,
):
    archive = tmp_path / "flashin_release.zip"
    calls = []

    monkeypatch.setattr(
        deploy_gate,
        "verify_deploy_release",
        lambda _root, requested_archive: {
            "ok": True,
            "archive": str(requested_archive),
            "git_commit": SHA,
            "errors": [],
        },
    )

    def fake_provenance(expected_sha, *, repository, api_base, token):
        calls.append((expected_sha, repository, api_base, token))
        return {
            "ok": True,
            "repository": repository,
            "branch": "main",
            "sha": expected_sha,
            "branch_protected": True,
            "exact_push_ci_run_id": 4242,
            "errors": [],
        }

    monkeypatch.setattr(
        deploy_gate,
        "verify_deploy_repository_provenance",
        fake_provenance,
    )

    exit_code = deploy_gate.main(
        [
            "--archive",
            str(archive),
            "--print-path",
        ]
    )

    assert exit_code == 0
    assert calls == [
        (
            SHA,
            "PetrFedin/flashin-miniapp",
            "https://api.github.com",
            "",
        )
    ]
    assert capsys.readouterr().out.strip() == str(archive)


def test_cli_fails_closed_when_repository_provenance_fails(
    monkeypatch,
    tmp_path,
    capsys,
):
    archive = tmp_path / "flashin_release.zip"
    monkeypatch.setattr(
        deploy_gate,
        "verify_deploy_release",
        lambda _root, requested_archive: {
            "ok": True,
            "archive": str(requested_archive),
            "git_commit": SHA,
            "errors": [],
        },
    )
    monkeypatch.setattr(
        deploy_gate,
        "verify_deploy_repository_provenance",
        lambda *_args, **_kwargs: {
            "ok": False,
            "errors": ["main is not protected"],
        },
    )

    exit_code = deploy_gate.main(
        ["--archive", str(archive), "--print-path"]
    )
    output = capsys.readouterr().out

    assert exit_code == 1
    assert "repository provenance: main is not protected" in output
    assert output.strip() != str(archive)
