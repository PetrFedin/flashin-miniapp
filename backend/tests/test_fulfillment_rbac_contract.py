from pathlib import Path
from types import SimpleNamespace

from backend.services.rbac import (
    DEFAULT_PERMISSIONS,
    FULFILLMENT_READ_PERMISSION,
    has_permission,
)


class _Query:
    def filter(self, *_args, **_kwargs):
        return self

    def all(self):
        return []


class _Db:
    def query(self, _model):
        return _Query()


def _fulfillment_source() -> str:
    return (
        Path(__file__).resolve().parents[1] / "api" / "fulfillment.py"
    ).read_text(encoding="utf-8")


def _route_block(source: str, marker: str, next_marker: str | None) -> str:
    block = source.split(marker, 1)[1]
    if next_marker:
        block = block.split(next_marker, 1)[0]
    return block


def test_fulfillment_read_defaults_are_least_privilege():
    db = _Db()

    assert FULFILLMENT_READ_PERMISSION in DEFAULT_PERMISSIONS["manager"]
    assert FULFILLMENT_READ_PERMISSION in DEFAULT_PERMISSIONS["warehouse"]
    assert FULFILLMENT_READ_PERMISSION not in DEFAULT_PERMISSIONS["support"]
    assert has_permission(db, SimpleNamespace(role="manager"), FULFILLMENT_READ_PERMISSION) is True
    assert has_permission(db, SimpleNamespace(role="warehouse"), FULFILLMENT_READ_PERMISSION) is True
    assert has_permission(db, SimpleNamespace(role="support"), FULFILLMENT_READ_PERMISSION) is False
    assert has_permission(db, SimpleNamespace(role="owner"), FULFILLMENT_READ_PERMISSION) is True


def test_fulfillment_read_routes_use_dedicated_permission():
    source = _fulfillment_source()
    read_routes = (
        ("def list_tasks(", "def update_task("),
        ("def list_sla(", "def task_picklist("),
        ("def task_picklist(", "def update_task_item("),
    )

    for marker, next_marker in read_routes:
        block = _route_block(source, marker, next_marker)
        assert "require_permission(db, admin, FULFILLMENT_READ_PERMISSION)" in block
        assert 'require_permission(db, admin, "orders.read")' not in block


def test_fulfillment_write_routes_keep_write_permission():
    source = _fulfillment_source()
    update_task = _route_block(source, "def update_task(", "def list_sla(")
    update_item = _route_block(source, "def update_task_item(", None)

    assert 'require_permission(db, admin, "fulfillment.write")' in update_task
    assert 'require_permission(db, admin, "fulfillment.write")' in update_item


def test_permission_migration_preserves_only_prior_fulfillment_readers():
    source = (
        Path(__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / "0035_fulfillment_read_permission.py"
    ).read_text(encoding="utf-8")

    assert 'down_revision = "0034_notification_policy_context"' in source
    assert "role IN ('manager', 'warehouse')" in source
    assert "permission = 'orders.read'" in source
    assert "existing.permission = 'fulfillment.read'" in source
    assert "NOT EXISTS" in source
    assert "role IN ('support'" not in source
