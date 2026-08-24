from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from backend.api import admin_security as admin_security_api
from backend.models import AdminIpAllowlist
from backend.schemas import AdminIpAllowlistIn


class QueryStub:
    def __init__(self, db):
        self.db = db

    def filter(self, *args, **kwargs):
        return self

    def with_for_update(self):
        return self

    def all(self):
        return [row for row in self.db.rows if bool(row.active)]

    def first(self):
        return self.db.rows[0] if self.db.rows else None


class FakeDb:
    def __init__(self, rows=None):
        self.rows = list(rows or [])
        self.commits = 0
        self.rollbacks = 0
        self.refreshes = 0

    def query(self, model):
        assert model is AdminIpAllowlist
        return QueryStub(self)

    def add(self, row):
        if row not in self.rows:
            self.rows.append(row)

    def flush(self):
        for index, row in enumerate(self.rows, start=1):
            if getattr(row, "id", None) is None:
                row.id = index

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def refresh(self, row):
        self.refreshes += 1


def _request(host="198.51.100.20", *, forwarded_for=None):
    headers = []
    if forwarded_for is not None:
        headers.append((b"x-forwarded-for", forwarded_for.encode("ascii")))
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "POST",
            "scheme": "https",
            "path": "/api/admin-security/ip-allowlist",
            "raw_path": b"/api/admin-security/ip-allowlist",
            "query_string": b"",
            "headers": headers,
            "client": (host, 54321),
            "server": ("admin.flashin.store", 443),
        }
    )


def _admin():
    return SimpleNamespace(id=7, role="owner", email="owner@flashin.test")


def _rule(cidr, *, active=True, rule_id=None):
    return AdminIpAllowlist(
        id=rule_id,
        cidr=cidr,
        description="",
        active=active,
    )


def _patch_route_dependencies(monkeypatch, *, app_env="test"):
    audits = []
    monkeypatch.setattr(admin_security_api, "require_permission", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        admin_security_api,
        "get_settings",
        lambda: SimpleNamespace(app_env=app_env),
    )
    monkeypatch.setattr(
        admin_security_api,
        "log_admin_action",
        lambda db, admin, action, entity_type, entity_id, payload: audits.append(
            (action, entity_type, entity_id, payload)
        ),
    )
    return audits


def test_first_active_rule_that_excludes_current_admin_is_rejected(monkeypatch):
    audits = _patch_route_dependencies(monkeypatch)
    db = FakeDb()

    with pytest.raises(HTTPException) as exc:
        admin_security_api.add_ip_rule(
            payload=AdminIpAllowlistIn(cidr="10.20.0.0/16", active=True),
            request=_request("198.51.100.20"),
            admin=_admin(),
            db=db,
        )

    assert exc.value.status_code == 409
    assert "lock out" in exc.value.detail
    assert db.commits == 0
    assert db.rollbacks == 1
    assert audits == []


def test_first_active_rule_matching_current_admin_is_committed_and_audited(monkeypatch):
    audits = _patch_route_dependencies(monkeypatch)
    db = FakeDb()

    row = admin_security_api.add_ip_rule(
        payload=AdminIpAllowlistIn(cidr="198.51.100.0/24", active=True),
        request=_request("198.51.100.20"),
        admin=_admin(),
        db=db,
    )

    assert row.cidr == "198.51.100.0/24"
    assert row.active is True
    assert db.commits == 1
    assert db.rollbacks == 0
    assert audits == [
        (
            "admin.ip_allowlist.create",
            "admin_ip_allowlist",
            row.id,
            {"cidr": "198.51.100.0/24", "active": True},
        )
    ]


def test_inactive_rule_can_be_staged_without_changing_access(monkeypatch):
    audits = _patch_route_dependencies(monkeypatch)
    db = FakeDb()

    row = admin_security_api.add_ip_rule(
        payload=AdminIpAllowlistIn(cidr="10.20.0.0/16", active=False),
        request=_request("198.51.100.20"),
        admin=_admin(),
        db=db,
    )

    assert row.active is False
    assert db.commits == 1
    assert audits[0][0] == "admin.ip_allowlist.create"


def test_production_cannot_disable_last_active_rule_via_api(monkeypatch):
    audits = _patch_route_dependencies(monkeypatch, app_env="production")
    db = FakeDb([_rule("198.51.100.0/24", active=True, rule_id=1)])

    with pytest.raises(HTTPException) as exc:
        admin_security_api.set_ip_rule_state(
            rule_id=1,
            payload=admin_security_api.AdminIpAllowlistStateIn(active=False),
            request=_request("172.18.0.4", forwarded_for="198.51.100.20"),
            admin=_admin(),
            db=db,
        )

    assert exc.value.status_code == 409
    assert "cannot be emptied" in exc.value.detail
    assert db.commits == 0
    assert db.rollbacks == 1
    assert audits == []


def test_rule_rotation_allows_old_network_to_be_disabled_after_new_matching_rule_exists(monkeypatch):
    audits = _patch_route_dependencies(monkeypatch, app_env="production")
    old_rule = _rule("198.51.100.0/25", active=True, rule_id=1)
    new_rule = _rule("198.51.100.0/24", active=True, rule_id=2)
    db = FakeDb([old_rule, new_rule])

    result = admin_security_api.set_ip_rule_state(
        rule_id=1,
        payload=admin_security_api.AdminIpAllowlistStateIn(active=False),
        request=_request("172.18.0.4", forwarded_for="198.51.100.20"),
        admin=_admin(),
        db=db,
    )

    assert result is old_rule
    assert old_rule.active is False
    assert new_rule.active is True
    assert db.commits == 1
    assert db.rollbacks == 0
    assert audits == [
        (
            "admin.ip_allowlist.state",
            "admin_ip_allowlist",
            1,
            {
                "cidr": "198.51.100.0/25",
                "before_active": True,
                "after_active": False,
            },
        )
    ]


def test_rotation_rejects_disabling_the_only_rule_that_covers_current_admin(monkeypatch):
    audits = _patch_route_dependencies(monkeypatch, app_env="production")
    current_rule = _rule("198.51.100.0/24", active=True, rule_id=1)
    other_rule = _rule("203.0.113.0/24", active=True, rule_id=2)
    db = FakeDb([current_rule, other_rule])

    with pytest.raises(HTTPException) as exc:
        admin_security_api.set_ip_rule_state(
            rule_id=1,
            payload=admin_security_api.AdminIpAllowlistStateIn(active=False),
            request=_request("172.18.0.4", forwarded_for="198.51.100.20"),
            admin=_admin(),
            db=db,
        )

    assert exc.value.status_code == 409
    assert "lock out" in exc.value.detail
    assert db.commits == 0
    assert db.rollbacks == 1
    assert audits == []
