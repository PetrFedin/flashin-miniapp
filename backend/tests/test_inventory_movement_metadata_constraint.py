from pathlib import Path

from sqlalchemy import CheckConstraint

from backend.models import InventoryMovement

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "alembic" / "versions" / "0026_inventory_return_movement.py"
DATABASE = ROOT / "database.py"


def test_inventory_movement_metadata_allows_return_like_production_schema():
    constraints = [
        constraint
        for constraint in InventoryMovement.__table__.constraints
        if isinstance(constraint, CheckConstraint)
        and constraint.name == "ck_inventory_movements_kind"
    ]
    assert len(constraints) == 1
    sqltext = str(constraints[0].sqltext)
    for kind in ("reserve", "release", "commit", "return"):
        assert f"'{kind}'" in sqltext


def test_metadata_adapter_and_alembic_migration_share_return_contract():
    database = DATABASE.read_text(encoding="utf-8")
    migration = MIGRATION.read_text(encoding="utf-8")
    expected = "kind IN ('reserve', 'release', 'commit', 'return')"
    assert expected in database
    assert expected in migration
