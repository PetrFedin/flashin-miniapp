import importlib.util
from pathlib import Path
from types import SimpleNamespace

import sqlalchemy as sa


ROOT = Path(__file__).resolve().parents[2]
MIGRATION_PATH = (
    ROOT
    / "backend"
    / "alembic"
    / "versions"
    / "0011_migrate_granular_rbac_permissions.py"
)


def load_migration():
    spec = importlib.util.spec_from_file_location("migration_0011", MIGRATION_PATH)
    migration = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(migration)
    return migration


def test_0011_migrates_legacy_grants_idempotently_and_preserves_custom_permissions():
    migration = load_migration()
    engine = sa.create_engine("sqlite://")
    metadata = sa.MetaData()
    permissions = sa.Table(
        "admin_role_permissions",
        metadata,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("role", sa.String(64), nullable=False),
        sa.Column("permission", sa.String(120), nullable=False),
    )
    metadata.create_all(engine)

    legacy_permissions = tuple(migration.PERMISSION_MIGRATIONS)
    expected_granular = {
        permission
        for granular_permissions in migration.PERMISSION_MIGRATIONS.values()
        for permission in granular_permissions
    }
    with engine.begin() as connection:
        connection.execute(
            permissions.insert(),
            [
                *(
                    {"role": "legacy-role", "permission": permission}
                    for permission in legacy_permissions
                ),
                {"role": "legacy-role", "permission": "custom.permission"},
                {"role": "partial-role", "permission": "orders.read"},
                {"role": "partial-role", "permission": "analytics.read"},
            ],
        )
        migration.op = SimpleNamespace(get_bind=lambda: connection)

        migration.upgrade()
        migration.upgrade()

        legacy_role_permissions = connection.execute(
            sa.select(permissions.c.permission).where(
                permissions.c.role == "legacy-role"
            )
        ).scalars().all()
        partial_role_permissions = connection.execute(
            sa.select(permissions.c.permission).where(
                permissions.c.role == "partial-role"
            )
        ).scalars().all()

    assert set(legacy_permissions) <= set(legacy_role_permissions)
    assert expected_granular <= set(legacy_role_permissions)
    assert "custom.permission" in legacy_role_permissions
    assert len(legacy_role_permissions) == len(set(legacy_role_permissions))
    assert partial_role_permissions.count("analytics.read") == 1
    assert set(migration.PERMISSION_MIGRATIONS["orders.read"]) <= set(
        partial_role_permissions
    )
    assert migration.down_revision == "0010_enterprise_telegram_commerce"


def test_0011_downgrade_is_safe_no_op():
    migration = load_migration()

    assert migration.downgrade() is None
