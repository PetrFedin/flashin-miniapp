from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_replay_anchor_migration_extends_current_pilot_runtime_head():
    source = (
        ROOT / "backend/alembic/versions/0023_pilot_state_replay_anchor.py"
    ).read_text(encoding="utf-8")
    assert 'revision = "0023_pilot_state_replay_anchor"' in source
    assert 'down_revision = "0022_pilot_runtime_guard"' in source
    assert '"pilot_state_revision"' in source
    assert '"pilot_state_sha256"' in source
    assert "ck_pilot_runtime_state_anchor" in source
