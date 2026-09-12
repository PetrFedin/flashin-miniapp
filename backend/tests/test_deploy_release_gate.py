import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from deploy_release_gate import verify_deploy_release  # noqa: E402
from release_control import create_release  # noqa: E402


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "pilot@example.com")
    git(repo, "config", "user.name", "Pilot Test")
    (repo / ".gitignore").write_text(
        ".env\ndeploy/release/builds/\ndeploy/release/runtime/\nbackups/\n",
        encoding="utf-8",
    )
    (repo / "backend").mkdir()
    (repo / "scripts").mkdir()
    (repo / "backend" / "app.py").write_text("print('ok')\n", encoding="utf-8")
    runner = repo / "scripts" / "run.sh"
    runner.write_text("#!/usr/bin/env bash\necho ok\n", encoding="utf-8")
    runner.chmod(0o755)
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "initial")
    return repo


def make_release(repo: Path, release_id: str = "pilot") -> Path:
    result = create_release(
        repo,
        repo / "deploy/release/builds",
        release_id=release_id,
        created_at="2026-08-10T09:00:00Z",
    )
    return Path(result["archive"])


def test_exact_retained_release_and_clean_checkout_are_accepted(tmp_path):
    repo = make_repo(tmp_path)
    archive = make_release(repo)
    report = verify_deploy_release(repo, archive)
    assert report["ok"], report
    assert report["git_commit"]
    assert report["file_count"] >= 3


def test_release_must_be_retained_under_build_directory(tmp_path):
    repo = make_repo(tmp_path)
    result = create_release(
        repo,
        tmp_path / "outside",
        release_id="outside",
        created_at="2026-08-10T09:00:00Z",
    )
    report = verify_deploy_release(repo, Path(result["archive"]))
    assert not report["ok"]
    assert any("retained under deploy/release/builds" in item for item in report["errors"])


def test_checksum_sidecar_is_mandatory(tmp_path):
    repo = make_repo(tmp_path)
    archive = make_release(repo)
    archive.with_suffix(archive.suffix + ".sha256").unlink()
    report = verify_deploy_release(repo, archive)
    assert not report["ok"]
    assert "deployment release checksum sidecar is missing" in report["errors"]


def test_tracked_checkout_drift_is_rejected(tmp_path):
    repo = make_repo(tmp_path)
    archive = make_release(repo)
    (repo / "backend" / "app.py").write_text("print('drift')\n", encoding="utf-8")
    report = verify_deploy_release(repo, archive)
    assert not report["ok"]
    assert any("checkout is not clean" in item for item in report["errors"])
    assert any("differs from release artifact" in item for item in report["errors"])


def test_nonignored_untracked_build_context_is_rejected(tmp_path):
    repo = make_repo(tmp_path)
    archive = make_release(repo)
    (repo / "backend" / "rogue.py").write_text("raise SystemExit\n", encoding="utf-8")
    report = verify_deploy_release(repo, archive)
    assert not report["ok"]
    assert any("checkout is not clean" in item for item in report["errors"])


def test_archive_from_other_commit_is_rejected(tmp_path):
    repo = make_repo(tmp_path)
    archive = make_release(repo, "old")
    (repo / "backend" / "app.py").write_text("print('new')\n", encoding="utf-8")
    git(repo, "add", "backend/app.py")
    git(repo, "commit", "-qm", "new")
    report = verify_deploy_release(repo, archive)
    assert not report["ok"]
    assert "release manifest git_commit does not match checkout HEAD" in report["errors"]


def test_non_executable_permission_differences_are_tolerated(tmp_path):
    repo = make_repo(tmp_path)
    archive = make_release(repo)
    source = repo / "backend" / "app.py"
    source.chmod(0o664)
    report = verify_deploy_release(repo, archive)
    assert report["ok"], report


def test_executable_mode_drift_is_rejected(tmp_path):
    repo = make_repo(tmp_path)
    archive = make_release(repo)
    source = repo / "backend" / "app.py"
    source.chmod(0o755)
    report = verify_deploy_release(repo, archive)
    assert not report["ok"]
    assert any("executable mode differs" in item for item in report["errors"])


def test_deploy_verifies_release_before_runtime_stop_and_builds_from_extracted_artifact():
    deploy = (ROOT / "scripts" / "deploy_production.sh").read_text(encoding="utf-8")
    gate = "deploy_release_gate.py --archive"
    stop = "pilot_runtime.py _stop"
    extract = "release_control.py extract"
    build_context = 'cd "$release_source_dir"'

    assert gate in deploy
    assert stop in deploy
    assert deploy.index(gate) < deploy.index(stop)
    assert extract in deploy
    assert deploy.index(extract) < deploy.index(stop)
    assert build_context in deploy
    assert deploy.index(build_context) < deploy.index("docker compose", deploy.index(build_context))
    assert "release_control.py create --print-path" not in deploy
    assert "RELEASE=deploy/release/builds/flashin_<release>.zip make deploy-prod" in deploy
