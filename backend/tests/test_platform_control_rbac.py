from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from backend.api import platform as platform_api
from backend.models import AdminRolePermission, FeatureFlag, RemoteConfig
from backend.services.rbac import DEFAULT_PERMISSIONS, has_permission


class QueryStub:
    def __init__(self, rows):
        self.rows = list(rows)

    def filter(self, *args, **kwargs):
        return self

    def with_for_update(self):
        return self

    def first(self):
        return self.rows[0] if self.rows else None

    def all(self):
        return list(self.rows)


class PermissionOnlyDb:
    def __init__(self, permission_rows=None):
        self.permission_rows = list(permission_rows or [])
        self.non_permission_queries = []

    def query(self, model):
        if model is AdminRolePermission:
            return QueryStub(self.permission_rows)
        self.non_permission_queries.append(model)
        raise AssertionError(f"Unauthorized request reached domain query: {model}")


class MutationDb:
    def __init__(self, model, row):
        self.model = model
        self.row = row
        self.commits = 0
        self.rollbacks = 0
        self.flushes = 0
        self.refreshes = []

    def query(self, model):
        assert model is self.model
        return QueryStub([self.row])

    def add(self, row):
        self.row = row

    def flush(self):
        self.flushes += 1

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def refresh(self, row):
        self.refreshes.append(row)


def _manager():
    return SimpleNamespace(id=11, role="manager", email="manager@flashin.test")


def _owner():
    return SimpleNamespace(id=1, role="owner", email="owner@flashin.test")


def test_default_manager_orders_write_does_not_grant_platform_control_or_event_replay():
    assert "orders.write" in DEFAULT_PERMISSIONS["manager"]
    assert "platform.write" not in DEFAULT_PERMISSIONS["manager"]
    assert "events.replay" not in DEFAULT_PERMISSIONS["manager"]

    db = PermissionOnlyDb()
    manager = _manager()

    with pytest.raises(HTTPException) as feature_exc:
        platform_api.upsert_feature(
            SimpleNamespace(key="pilot_mode", enabled=True, description=""),
            admin=manager,
            db=db,
        )
    assert feature_exc.value.status_code == 403
    assert feature_exc.value.detail == "Missing permission: platform.write"

    with pytest.raises(HTTPException) as config_exc:
        platform_api.upsert_remote_config(
            SimpleNamespace(key="checkout", value_json={"enabled": True}, description=""),
            admin=manager,
            db=db,
        )
    assert config_exc.value.status_code == 403
    assert config_exc.value.detail == "Missing permission: platform.write"

    with pytest.raises(HTTPException) as replay_exc:
        platform_api.replay_event(
            77,
            SimpleNamespace(reason="Root cause fixed", payload=None),
            admin=manager,
            db=db,
        )
    assert replay_exc.value.status_code == 403
    assert replay_exc.value.detail == "Missing permission: events.replay"
    assert db.non_permission_queries == []


def test_owner_wildcard_and_explicit_custom_grants_cover_new_permissions():
    db = PermissionOnlyDb()
    owner = _owner()
    assert has_permission(db, owner, "platform.write") is True
    assert has_permission(db, owner, "events.replay") is True

    custom = SimpleNamespace(id=22, role="operator", email="operator@flashin.test")
    custom_db = PermissionOnlyDb(
        [
            SimpleNamespace(permission="platform.write"),
            SimpleNamespace(permission="events.replay"),
        ]
    )
    assert has_permission(custom_db, custom, "platform.write") is True
    assert has_permission(custom_db, custom, "events.replay") is True
    assert has_permission(custom_db, custom, "orders.write") is False


def test_feature_flag_write_is_locked_and_audited(monkeypatch):
    row = SimpleNamespace(id=31, key="pilot_mode", enabled=False, description="old")
    db = MutationDb(FeatureFlag, row)
    audits = []
    monkeypatch.setattr(platform_api, "log_admin_action", lambda *args: audits.append(args))

    result = platform_api.upsert_feature(
        SimpleNamespace(key="pilot_mode", enabled=True, description="controlled"),
        admin=_owner(),
        db=db,
    )

    assert result is row
    assert row.enabled is True
    assert row.description == "controlled"
    assert db.commits == 1
    assert db.rollbacks == 0
    assert db.refreshes == [row]
    assert len(audits) == 1
    assert audits[0][2] == "platform.feature_flag.upsert"
    assert audits[0][-1] == {"key": "pilot_mode", "enabled": True}


def test_remote_config_write_audit_never_duplicates_config_value(monkeypatch):
    row = SimpleNamespace(id=41, key="provider", value_json="{}", description="old")
    db = MutationDb(RemoteConfig, row)
    audits = []
    monkeypatch.setattr(platform_api, "log_admin_action", lambda *args: audits.append(args))
    secret_value = {"private_token": "must-not-enter-audit"}

    result = platform_api.upsert_remote_config(
        SimpleNamespace(key="provider", value_json=secret_value, description="runtime"),
        admin=_owner(),
        db=db,
    )

    assert result is row
    assert "must-not-enter-audit" in row.value_json
    assert row.description == "runtime"
    assert db.commits == 1
    assert db.rollbacks == 0
    assert len(audits) == 1
    assert audits[0][2] == "platform.remote_config.upsert"
    assert audits[0][-1] == {"key": "provider"}
    assert "must-not-enter-audit" not in repr(audits)
