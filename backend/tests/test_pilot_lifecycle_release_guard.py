import json
from pathlib import Path
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import pilot_lifecycle_release_guard as guard  # noqa: E402


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


def test_old_runtime_capability_archive_cannot_receive_lifecycle_admission(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        guard,
        "verify_release",
        lambda _archive: {
            "ok": True,
            "errors": [],
            "release_id": "release-old",
            "git_commit": "a" * 40,
            "sha256": "b" * 64,
        },
    )
    archive = _archive(
        tmp_path / "old.zip",
        {"scripts/pilot_control_binding.py": "def build_admission_binding(): pass"},
    )

    errors = guard.inspect_lifecycle_release(archive)

    assert any("missing file" in error for error in errors)
    assert any("scripts/pilot_live_lifecycle.py" in error for error in errors)


def test_exact_release_requires_every_lifecycle_file_and_marker(tmp_path, monkeypatch):
    monkeypatch.setattr(
        guard,
        "verify_release",
        lambda _archive: {
            "ok": True,
            "errors": [],
            "release_id": "release-live",
            "git_commit": "c" * 40,
            "sha256": "d" * 64,
        },
    )
    files = _complete_files()
    archive = _archive(tmp_path / "valid.zip", files)
    assert guard.inspect_lifecycle_release(archive) == []

    broken = dict(files)
    broken["scripts/pilot_live_lifecycle.py"] = "BASE_REQUIRED_SCENARIOS"
    archive = _archive(tmp_path / "broken.zip", broken)
    errors = guard.inspect_lifecycle_release(archive)
    assert any("pilot_live_lifecycle" in error for error in errors)


def test_current_release_pointer_must_match_lifecycle_archive(tmp_path, monkeypatch):
    files = _complete_files()
    archive = _archive(tmp_path / "release.zip", files)
    runtime = tmp_path / "deploy/release/runtime"
    runtime.mkdir(parents=True)
    (runtime / "current_release.json").write_text(
        json.dumps(
            {
                "release_id": "release-live",
                "git_commit": "c" * 40,
                "sha256": "wrong",
                "archive": str(archive),
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        guard,
        "verify_release",
        lambda _archive: {
            "ok": True,
            "errors": [],
            "release_id": "release-live",
            "git_commit": "c" * 40,
            "sha256": "d" * 64,
        },
    )

    try:
        guard.require_current_lifecycle_release(tmp_path)
    except ValueError as exc:
        assert "sha256 does not match archive" in str(exc)
    else:
        raise AssertionError("release pointer mismatch must fail closed")
