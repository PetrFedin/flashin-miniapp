import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from pilot_release_capability import inspect_runtime_guard  # noqa: E402
from release_control import create_release  # noqa: E402


def test_current_repository_archive_satisfies_capability_v14(tmp_path):
    state = create_release(
        ROOT,
        tmp_path / "builds",
        release_id="current-capability-v14",
        created_at="2026-08-05T00:00:00Z",
    )

    assert inspect_runtime_guard(Path(state["archive"])) == []
