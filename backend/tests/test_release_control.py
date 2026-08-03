import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from release_control import (  # noqa: E402
    MANIFEST_NAME,
    create_release,
    extract_release,
    promote_release,
    resolve_release,
    verify_release,
)


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "pilot@example.com")
    git(repo, "config", "user.name", "Pilot Test")
    (repo / ".gitignore").write_text(".env\n", encoding="utf-8")
    (repo / ".env").write_text("SECRET=never-package\n", encoding="utf-8")
    (repo / ".env.production.example").write_text("SECRET=replace-me\n", encoding="utf-8")
    (repo / "app.py").write_text("print('flashin')\n", encoding="utf-8")
    (repo / "scripts").mkdir()
    executable = repo / "scripts" / "run.sh"
    executable.write_text("#!/usr/bin/env bash\necho ok\n", encoding="utf-8")
    executable.chmod(0o755)
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "initial")
    return repo


def test_release_is_deterministic_and_excludes_secrets(tmp_path):
    repo = make_repo(tmp_path)
    first = create_release(
        repo,
        tmp_path / "one",
        release_id="pilot-1",
        created_at="2026-08-03T12:00:00Z",
    )
    second = create_release(
        repo,
        tmp_path / "two",
        release_id="pilot-1",
        created_at="2026-08-03T12:00:00Z",
    )
    assert Path(first["archive"]).read_bytes() == Path(second["archive"]).read_bytes()
    report = verify_release(Path(first["archive"]))
    assert report["ok"], report
    with zipfile.ZipFile(first["archive"]) as bundle:
        assert ".env" not in bundle.namelist()
        assert ".env.production.example" in bundle.namelist()
        assert json.loads(bundle.read(MANIFEST_NAME))["policy"]["secrets_included"] is False


def test_release_creation_fails_for_dirty_tracked_checkout(tmp_path):
    repo = make_repo(tmp_path)
    (repo / "app.py").write_text("changed\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="Tracked files are modified"):
        create_release(repo, tmp_path / "builds", release_id="dirty")


def test_verifier_rejects_zip_slip_and_forbidden_secret(tmp_path):
    archive = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("../escape", b"bad")
        bundle.writestr(".env", b"SECRET=x")
        bundle.writestr(
            MANIFEST_NAME,
            json.dumps({"schema_version": 1, "files": {}, "file_count": 0}),
        )
    report = verify_release(archive)
    assert not report["ok"]
    assert any("Unsafe archive path" in error for error in report["errors"])
    assert any("Forbidden release path" in error for error in report["errors"])


def test_verifier_detects_manifest_payload_mismatch(tmp_path):
    repo = make_repo(tmp_path)
    release = create_release(repo, tmp_path / "builds", release_id="tamper")
    archive = Path(release["archive"])
    rewritten = tmp_path / "tampered.zip"
    with zipfile.ZipFile(archive) as source, zipfile.ZipFile(rewritten, "w") as target:
        for info in source.infolist():
            data = source.read(info.filename)
            if info.filename == "app.py":
                data = b"tampered\n"
            target.writestr(info, data)
    report = verify_release(rewritten)
    assert not report["ok"]
    assert "SHA256 mismatch: app.py" in report["errors"]


def test_extract_preserves_executable_mode(tmp_path):
    repo = make_repo(tmp_path)
    release = create_release(repo, tmp_path / "builds", release_id="extract")
    destination = tmp_path / "out"
    extract_release(Path(release["archive"]), destination)
    assert (destination / "app.py").read_text(encoding="utf-8") == "print('flashin')\n"
    assert os.access(destination / "scripts" / "run.sh", os.X_OK)
    assert not (destination / ".env").exists()


def test_promote_rotates_current_to_previous(tmp_path):
    repo = make_repo(tmp_path)
    first = create_release(repo, tmp_path / "builds", release_id="first")
    (repo / "app.py").write_text("print('second')\n", encoding="utf-8")
    git(repo, "add", "app.py")
    git(repo, "commit", "-qm", "second")
    second = create_release(repo, tmp_path / "builds", release_id="second")
    state_dir = tmp_path / "state"
    promote_release(Path(first["archive"]), state_dir)
    result = promote_release(Path(second["archive"]), state_dir)
    assert result["current"]["release_id"] == "second"
    assert result["previous"]["release_id"] == "first"
    assert resolve_release("current", state_dir) == Path(second["archive"]).resolve()
    assert resolve_release("previous", state_dir) == Path(first["archive"]).resolve()
