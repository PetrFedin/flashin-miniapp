from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_replay_anchor_migration_extends_pilot_runtime_guard():
    source = (
        ROOT / "backend/alembic/versions/0023_pilot_state_replay_anchor.py"
    ).read_text(encoding="utf-8")

    assert 'revision = "0023_pilot_state_replay_anchor"' in source
    assert 'down_revision = "0022_pilot_runtime_guard"' in source
    assert '"pilot_state_revision"' in source
    assert '"pilot_state_sha256"' in source
    assert "ck_pilot_runtime_state_anchor" in source


def test_inventory_ledger_migration_extends_replay_anchor():
    source = (
        ROOT / "backend/alembic/versions/0024_inventory_movement_ledger.py"
    ).read_text(encoding="utf-8")

    assert 'revision = "0024_inventory_movement_ledger"' in source
    assert 'down_revision = "0023_pilot_state_replay_anchor"' in source
    assert '"inventory_movements"' in source
    assert "uq_inventory_movement_order_variant_kind" in source
