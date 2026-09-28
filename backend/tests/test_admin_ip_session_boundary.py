from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from starlette.requests import Request

from backend import security
from backend.models import AdminIpAllowlist, AdminUser


class FakeQuery:
    def __init__(self, values):
        self.values = list(values)

    def filter(self, *args, **kwargs):
        return self

    def first(self):
        return self.values[0] if self.values else None

    def all(self):
        return list(self.values)


class FakeDb:
    def __init__(self, *, admin, rules):
        self.admin = admin
        self.rules = list(rules)
        self.mutations = 0

    def query(self, model):
        if model is AdminUser:
            return FakeQuery([self.admin] if self.admin else [])
        if model is AdminIpAllowlist:
            return FakeQuery(self.rules)
        raise AssertionError(f"Unexpected model query: {model}")

    def add(self, *args, **kwargs):
        self.mutations += 1

    def delete(self, *args, **kwargs):
        self.mutations += 1

    def commit(self):
        self.mutations += 1


def _request(host: str, *, forwarded_for: str | None = None) -> Request:
    headers = []
    if forwarded_for is not None:
        headers.append((b"x-forwarded-for", forwarded_for.encode("ascii")))
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "GET",
            "scheme": "https",
            "path": "/api/admin/session",
            "raw_path": b"/api/admin/session",
            "query_string": b"",
            "headers": headers,
            "client": (host, 54321),
            "server": ("admin.flashin.store", 443),
        }
    )


def _credentials() -> HTTPAuthorizationCredentials:
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials="existing-admin-token")


def _patch_authenticated_admin(monkeypatch, *, app_env: str = "test"):
    admin = SimpleNamespace(id=17, email="owner@flashin.test", role="owner", active=True)
    monkeypatch.setattr(
        security,
        "_decode_token",
        lambda *args, **kwargs: {"sub": "admin:17"},
    )
    monkeypatch.setattr(security, "is_admin_session_active", lambda *args, **kwargs: True)
    monkeypatch.setattr(security, "get_settings", lambda: SimpleNamespace(app_env=app_env))
    return admin


def test_existing_admin_bearer_remains_valid_when_no_ip_allowlist_is_configured(monkeypatch):
    admin = _patch_authenticated_admin(monkeypatch)
    db = FakeDb(admin=admin, rules=[])

    resolved = security.get_current_admin(
        request=_request("198.51.100.20"),
        credentials=_credentials(),
        db=db,
    )

    assert resolved is admin
    assert db.mutations == 0


def test_existing_admin_bearer_is_allowed_from_matching_network(monkeypatch):
    admin = _patch_authenticated_admin(monkeypatch)
    db = FakeDb(
        admin=admin,
        rules=[SimpleNamespace(cidr="198.51.100.0/24", active=True)],
    )

    resolved = security.get_current_admin(
        request=_request("198.51.100.20"),
        credentials=_credentials(),
        db=db,
    )

    assert resolved is admin
    assert db.mutations == 0


def test_existing_admin_bearer_is_blocked_from_nonmatching_network_without_revocation(monkeypatch):
    admin = _patch_authenticated_admin(monkeypatch)
    session_checks = []
    monkeypatch.setattr(
        security,
        "is_admin_session_active",
        lambda db, admin_id, token: session_checks.append((admin_id, token)) or True,
    )
    db = FakeDb(
        admin=admin,
        rules=[SimpleNamespace(cidr="10.20.0.0/16", active=True)],
    )

    with pytest.raises(HTTPException) as exc_info:
        security.get_current_admin(
            request=_request("198.51.100.20"),
            credentials=_credentials(),
            db=db,
        )

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "Admin access is not allowed"
    assert session_checks == [(17, "existing-admin-token")]
    assert db.mutations == 0


def test_allowlist_change_applies_to_next_request_without_relogin_or_session_revocation(monkeypatch):
    admin = _patch_authenticated_admin(monkeypatch)
    db = FakeDb(
        admin=admin,
        rules=[SimpleNamespace(cidr="10.20.0.0/16", active=True)],
    )
    request = _request("198.51.100.20")
    credentials = _credentials()

    with pytest.raises(HTTPException) as exc_info:
        security.get_current_admin(request=request, credentials=credentials, db=db)
    assert exc_info.value.status_code == 403

    db.rules = [SimpleNamespace(cidr="198.51.100.0/24", active=True)]
    assert security.get_current_admin(request=request, credentials=credentials, db=db) is admin
    assert db.mutations == 0


def test_production_bearer_uses_same_single_forwarded_ip_contract_as_admin_login(monkeypatch):
    admin = _patch_authenticated_admin(monkeypatch, app_env="production")
    db = FakeDb(
        admin=admin,
        rules=[SimpleNamespace(cidr="203.0.113.0/24", active=True)],
    )

    resolved = security.get_current_admin(
        request=_request("172.18.0.4", forwarded_for="203.0.113.25"),
        credentials=_credentials(),
        db=db,
    )
    assert resolved is admin

    with pytest.raises(HTTPException) as exc_info:
        security.get_current_admin(
            request=_request("172.18.0.4", forwarded_for="203.0.113.25, 198.51.100.7"),
            credentials=_credentials(),
            db=db,
        )
    assert exc_info.value.status_code == 403
    assert db.mutations == 0
