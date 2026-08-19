import json
from pathlib import Path
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import pilot_governance_release_guard as guard  # noqa: E402


def _archive(path: Path, files: dict[str, str]) -> Path:
    manifest = {"files": {name: {"sha256": "x" * 64} for name in files}}
    with zipfile.ZipFile(path, "w") as bundle:
        bundle.writestr("release_manifest.json", json.dumps(manifest))
        for name, content in files.items():
            bundle.writestr(name, content)
    return path


def _complete_files() -> dict[str, str]:
    return {
        name: "\n".join(markers)
        for name, markers in guard.REQUIRED_FILES.items()
    }


def _verification():
    return {
        "ok": True,
        "errors": [],
        "release_id": "release-governance",
        "git_commit": "c" * 40,
        "sha256": "d" * 64,
    }


def test_lifecycle_only_archive_cannot_receive_governance_admission(tmp_path, monkeypatch):
    monkeypatch.setattr(guard, "inspect_lifecycle_release", lambda _archive: [])
    monkeypatch.setattr(guard, "verify_release", lambda _archive: _verification())
    archive = _archive(
        tmp_path / "lifecycle-only.zip",
        {"scripts/pilot_live_lifecycle.py": "BASE_REQUIRED_SCENARIOS"},
    )

    errors = guard.inspect_governance_release(archive)

    assert any("missing file" in error for error in errors)
    assert any("scripts/pilot_repository_governance.py" in error for error in errors)


def test_exact_release_requires_every_governance_file_and_marker(tmp_path, monkeypatch):
    monkeypatch.setattr(guard, "inspect_lifecycle_release", lambda _archive: [])
    monkeypatch.setattr(guard, "verify_release", lambda _archive: _verification())
    files = _complete_files()
    archive = _archive(tmp_path / "valid.zip", files)
    assert guard.inspect_governance_release(archive) == []

    broken = dict(files)
    broken["scripts/pilot_repository_governance.py"] = "collect_snapshot("
    archive = _archive(tmp_path / "broken.zip", broken)
    errors = guard.inspect_governance_release(archive)
    assert any("pilot_repository_governance" in error for error in errors)


def test_governance_release_rejects_persisted_operator_token(tmp_path, monkeypatch):
    monkeypatch.setattr(guard, "inspect_lifecycle_release", lambda _archive: [])
    monkeypatch.setattr(guard, "verify_release", lambda _archive: _verification())
    files = _complete_files()
    files[".env.production.example"] += "\nPILOT_GITHUB_TOKEN=forbidden-persisted-token\n"
    archive = _archive(tmp_path / "persisted-token.zip", files)

    errors = guard.inspect_governance_release(archive)

    assert any("forbidden assignment" in error for error in errors)
    assert any("PILOT_GITHUB_TOKEN=" in error for error in errors)


def test_current_release_pointer_must_match_governance_archive(tmp_path, monkeypatch):
    monkeypatch.setattr(guard, "inspect_lifecycle_release", lambda _archive: [])
    files = _complete_files()
    archive = _archive(tmp_path / "release.zip", files)
    runtime = tmp_path / "deploy/release/runtime"
    runtime.mkdir(parents=True)
    (runtime / "current_release.json").write_text(
        json.dumps(
            {
                "release_id": "release-governance",
                "git_commit": "c" * 40,
                "sha256": "wrong",
                "archive": str(archive),
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(guard, "verify_release", lambda _archive: _verification())

    try:
        guard.require_current_governance_release(tmp_path)
    except ValueError as exc:
        assert "sha256 does not match archive" in str(exc)
    else:
        raise AssertionError("release pointer mismatch must fail closed")
