from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from backend.api import admin_auth
from backend.models import AdminTotpSecret, AdminUser
from scripts.check_admin_mfa import inspect_admin_mfa


ROOT = Path(__file__).resolve().parents[2]


class FakeQuery:
    def __init__(self, values):
        self.values = list(values)

    def filter(self, *args, **kwargs):
        return self

    def with_for_update(self):
        return self

    def order_by(self, *args, **kwargs):
        return self

    def first(self):
        return self.values[0] if self.values else None

    def all(self):
        return list(self.values)


class LoginDb:
    def __init__(self, admin, totp):
        self.admin = admin
        self.totp = totp
        self.commits = 0
        self.rollbacks = 0

    def query(self, model):
        if model is AdminUser:
            return FakeQuery([self.admin] if self.admin is not None else [])
        if model is AdminTotpSecret:
            return FakeQuery([self.totp] if self.totp is not None else [])
        raise AssertionError(f"Unexpected model query: {model}")

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


class StatusDb:
    def __init__(self, admins, enabled_totp_rows):
        self.admins = admins
        self.enabled_totp_rows = enabled_totp_rows

    def query(self, model):
        if model is AdminUser:
            return FakeQuery(self.admins)
        if model is AdminTotpSecret:
            return FakeQuery(self.enabled_totp_rows)
        raise AssertionError(f"Unexpected model query: {model}")


def _admin():
    return SimpleNamespace(
        id=17,
        email="owner@flashin.test",
        password_hash="stored-hash",
        role="owner",
        active=True,
    )


def _patch_login_dependencies(monkeypatch, *, production: bool, reasons: list[str]):
    monkeypatch.setattr(admin_auth, "_request_identity", lambda request: ("127.0.0.1", "pytest"))
    monkeypatch.setattr(admin_auth, "is_admin_ip_allowed", lambda db, ip: True)
    monkeypatch.setattr(admin_auth, "verify_password", lambda password, password_hash: True)
    monkeypatch.setattr(admin_auth, "password_needs_rehash", lambda password_hash: False)
    monkeypatch.setattr(admin_auth, "_production_admin_mfa_required", lambda: production)
    monkeypatch.setattr(
        admin_auth,
        "log_admin_login",
        lambda db, email, admin_id, success, reason, ip, user_agent: reasons.append(reason),
    )


@pytest.mark.parametrize("totp", [None, SimpleNamespace(enabled=False, secret="unused")])
def test_production_login_refuses_full_session_without_enrolled_mfa(monkeypatch, totp):
    reasons: list[str] = []
    admin = _admin()
    db = LoginDb(admin, totp)
    _patch_login_dependencies(monkeypatch, production=True, reasons=reasons)

    def unexpected_token(*args, **kwargs):
        raise AssertionError("Production password-only login must never mint an admin token")

    monkeypatch.setattr(admin_auth, "create_admin_token", unexpected_token)

    with pytest.raises(HTTPException) as exc_info:
        admin_auth.admin_session_login(
            admin_auth.AdminSessionLoginIn(
                email=admin.email,
                password="CorrectHorseBatteryStaple!",
            ),
            request=None,
            db=db,
        )

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "Admin MFA enrollment is required"
    assert reasons == ["mfa_not_enrolled"]
    assert db.commits == 1


def test_nonproduction_login_keeps_existing_optional_mfa_behavior(monkeypatch):
    reasons: list[str] = []
    admin = _admin()
    db = LoginDb(admin, None)
    _patch_login_dependencies(monkeypatch, production=False, reasons=reasons)
    monkeypatch.setattr(admin_auth, "create_admin_token", lambda *args: "admin-token")
    monkeypatch.setattr(admin_auth, "create_admin_session", lambda *args: None)

    response = admin_auth.admin_session_login(
        admin_auth.AdminSessionLoginIn(
            email=admin.email,
            password="CorrectHorseBatteryStaple!",
        ),
        request=None,
        db=db,
    )

    assert response.access_token == "admin-token"
    assert reasons == ["success"]
    assert db.commits == 1


def test_admin_mfa_gate_requires_every_active_admin():
    admins = [
        SimpleNamespace(id=1, email="owner@flashin.test", role="owner"),
        SimpleNamespace(id=2, email="ops@flashin.test", role="manager"),
    ]
    enabled = [SimpleNamespace(admin_id=1, enabled=True)]

    report = inspect_admin_mfa(StatusDb(admins, enabled))

    assert report == {
        "ok": False,
        "active_admins": 2,
        "missing_mfa": ["ops@flashin.test"],
    }


def test_admin_mfa_gate_passes_only_when_all_active_admins_are_enrolled():
    admins = [
        SimpleNamespace(id=1, email="owner@flashin.test", role="owner"),
        SimpleNamespace(id=2, email="ops@flashin.test", role="manager"),
    ]
    enabled = [
        SimpleNamespace(admin_id=1, enabled=True),
        SimpleNamespace(admin_id=2, enabled=True),
    ]

    report = inspect_admin_mfa(StatusDb(admins, enabled))

    assert report["ok"] is True
    assert report["missing_mfa"] == []


def test_existing_admin_provisioning_uses_canonical_password_hashing():
    seed_admin = (ROOT / "scripts" / "seed_admin.py").read_text(encoding="utf-8")

    assert "from backend.security import hash_password" in seed_admin
    assert "hashlib.sha256" not in seed_admin
    assert "password_hash=hash_password(password)" in seed_admin


def test_production_deploy_fails_closed_on_admin_mfa_gate():
    deploy = (ROOT / "scripts" / "deploy_production.sh").read_text(encoding="utf-8")
    login = (ROOT / "backend" / "api" / "admin_auth.py").read_text(encoding="utf-8")

    assert "python scripts/check_admin_mfa.py" in deploy
    assert "mfa_not_enrolled" in login
    assert "Admin MFA enrollment is required" in login
