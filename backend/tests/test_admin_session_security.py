from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from backend import security
from backend.main import app
from backend.services.admin_security import normalize_totp_secret, verify_totp


class FakeQuery:
    def __init__(self, value):
        self.value = value

    def filter(self, *args, **kwargs):
        return self

    def first(self):
        return self.value


class FakeDb:
    def __init__(self, admin):
        self.admin = admin

    def query(self, model):
        return FakeQuery(self.admin)


def test_new_admin_login_route_is_registered_before_legacy_route():
    matching = [
        route
        for route in app.routes
        if getattr(route, "path", None) == "/api/admin/login"
        and "POST" in getattr(route, "methods", set())
    ]

    assert len(matching) >= 2
    assert matching[0].endpoint.__name__ == "admin_session_login"


def test_totp_matches_standard_hotp_counter_vector():
    secret = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"

    assert normalize_totp_secret(secret.lower()) == secret
    assert verify_totp(secret, "287082", at_time=59, window=0)
    assert not verify_totp(secret, "287083", at_time=59, window=0)


def test_revoked_or_unknown_admin_session_is_rejected(monkeypatch):
    admin = SimpleNamespace(id=7, active=True)
    db = FakeDb(admin)
    token = security.create_admin_token(admin.id, "owner")
    credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
    monkeypatch.setattr(security, "is_admin_session_active", lambda *args: False)

    with pytest.raises(HTTPException) as exc_info:
        security.get_current_admin(credentials=credentials, db=db)

    assert exc_info.value.status_code == 401
    assert "revoked" in exc_info.value.detail.lower()


def test_registered_admin_session_is_accepted(monkeypatch):
    admin = SimpleNamespace(id=7, active=True)
    db = FakeDb(admin)
    token = security.create_admin_token(admin.id, "owner")
    credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
    monkeypatch.setattr(security, "is_admin_session_active", lambda *args: True)

    assert security.get_current_admin(credentials=credentials, db=db) is admin
