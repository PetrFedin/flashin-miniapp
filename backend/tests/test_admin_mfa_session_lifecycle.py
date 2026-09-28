from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from backend.api import admin_security
from backend.models import AdminTotpSecret, AdminUser
from backend.services import admin_security as admin_security_service


class FakeQuery:
    def __init__(self, values):
        self.values = list(values)

    def filter(self, *args, **kwargs):
        return self

    def with_for_update(self):
        return self

    def first(self):
        return self.values[0] if self.values else None


class TotpDb:
    def __init__(self, target=None, *, totp_row=None):
        self.target = target
        self.totp_row = totp_row
        self.commits = 0
        self.rollbacks = 0

    def query(self, model):
        if model is AdminUser:
            return FakeQuery([self.target] if self.target is not None else [])
        if model is AdminTotpSecret:
            return FakeQuery([self.totp_row] if self.totp_row is not None else [])
        raise AssertionError(f"Unexpected model query: {model}")

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


def _target(*, active: bool = True):
    return SimpleNamespace(
        id=17,
        email="owner@flashin.test",
        role="owner",
        active=active,
    )


def _actor():
    return SimpleNamespace(id=99, email="security@flashin.test", role="owner")


def _patch_common(monkeypatch):
    monkeypatch.setattr(admin_security, "require_permission", lambda *args, **kwargs: None)
    monkeypatch.setattr(admin_security, "log_admin_action", lambda *args, **kwargs: None)


def test_production_active_admin_totp_cannot_be_disabled(monkeypatch):
    _patch_common(monkeypatch)
    monkeypatch.setattr(admin_security, "_production_admin_mfa_required", lambda: True)
    target = _target(active=True)
    db = TotpDb(target)

    def unexpected_set(*args, **kwargs):
        raise AssertionError("Production active-admin MFA must fail before mutation")

    monkeypatch.setattr(admin_security, "set_totp_secret", unexpected_set)

    with pytest.raises(HTTPException) as exc_info:
        admin_security.configure_totp(
            target.id,
            admin_security.AdminTotpIn(
                secret="JBSWY3DPEHPK3PXP",
                enabled=False,
            ),
            admin=_actor(),
            db=db,
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == (
        "TOTP cannot be disabled for an active administrator in production"
    )
    assert db.commits == 0
    assert db.rollbacks == 0


def test_nonproduction_totp_disable_delegates_to_shared_service(monkeypatch):
    _patch_common(monkeypatch)
    monkeypatch.setattr(admin_security, "_production_admin_mfa_required", lambda: False)
    target = _target(active=True)
    db = TotpDb(target)
    configured: list[tuple[int, bool]] = []

    def set_secret(db, admin_id, secret, enabled):
        configured.append((admin_id, enabled))
        return SimpleNamespace(enabled=enabled)

    monkeypatch.setattr(admin_security, "set_totp_secret", set_secret)

    response = admin_security.configure_totp(
        target.id,
        admin_security.AdminTotpIn(
            secret="JBSWY3DPEHPK3PXP",
            enabled=False,
        ),
        admin=_actor(),
        db=db,
    )

    assert response == {"ok": True, "enabled": False}
    assert configured == [(target.id, False)]
    assert db.commits == 1
    assert db.rollbacks == 0


def test_production_inactive_admin_totp_disable_is_allowed_via_shared_service(monkeypatch):
    _patch_common(monkeypatch)
    monkeypatch.setattr(admin_security, "_production_admin_mfa_required", lambda: True)
    target = _target(active=False)
    db = TotpDb(target)
    configured: list[tuple[int, bool]] = []

    def set_secret(db, admin_id, secret, enabled):
        configured.append((admin_id, enabled))
        return SimpleNamespace(enabled=enabled)

    monkeypatch.setattr(admin_security, "set_totp_secret", set_secret)

    response = admin_security.configure_totp(
        target.id,
        admin_security.AdminTotpIn(
            secret="JBSWY3DPEHPK3PXP",
            enabled=False,
        ),
        admin=_actor(),
        db=db,
    )

    assert response == {"ok": True, "enabled": False}
    assert configured == [(target.id, False)]
    assert db.commits == 1
    assert db.rollbacks == 0


@pytest.mark.parametrize("enabled", [False, True])
def test_set_totp_secret_always_revokes_sessions_at_service_boundary(monkeypatch, enabled):
    row = SimpleNamespace(admin_id=17, secret="enc:v1:old", enabled=not enabled)
    db = TotpDb(totp_row=row)
    reset_admin_ids: list[int] = []
    revoked_admin_ids: list[int] = []

    monkeypatch.setattr(
        admin_security_service,
        "encrypt_totp_secret",
        lambda admin_id, secret: "enc:v1:new",
    )
    monkeypatch.setattr(
        admin_security_service,
        "reset_totp_replay_state",
        lambda db, admin_id: reset_admin_ids.append(admin_id),
    )
    monkeypatch.setattr(
        admin_security_service,
        "revoke_admin_sessions",
        lambda db, admin_id: revoked_admin_ids.append(admin_id) or 2,
    )

    result = admin_security_service.set_totp_secret(
        db,
        17,
        "JBSWY3DPEHPK3PXP",
        enabled=enabled,
    )

    assert result is row
    assert row.secret == "enc:v1:new"
    assert row.enabled is enabled
    assert reset_admin_ids == [17]
    assert revoked_admin_ids == [17]
