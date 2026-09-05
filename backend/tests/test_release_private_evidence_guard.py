from __future__ import annotations

import importlib.util
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"


def _load_guard():
    spec = importlib.util.spec_from_file_location(
        "flashin_release_private_evidence_guard",
        SCRIPTS / "release_private_evidence_guard.py",
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def test_guard_detects_force_tracked_private_evidence(tmp_path):
    module = _load_guard()
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "pilot@example.com")
    _git(repo, "config", "user.name", "Pilot Test")
    private = repo / "docs" / "pilot" / "evidence" / "telegram_real_auth.json"
    private.parent.mkdir(parents=True)
    private.write_text('{"private":true}\n', encoding="utf-8")
    _git(repo, "add", "-f", private.relative_to(repo).as_posix())

    assert module.tracked_private_evidence(repo) == [
        "docs/pilot/evidence/telegram_real_auth.json"
    ]


def test_current_repository_has_no_tracked_private_evidence():
    module = _load_guard()
    assert module.tracked_private_evidence(ROOT) == []


def test_repository_ignores_private_evidence_and_release_workflow_runs_guard():
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    release = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")

    assert "docs/pilot/evidence/" in gitignore
    assert "python3 scripts/release_private_evidence_guard.py" in release
