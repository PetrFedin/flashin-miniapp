from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from backend.api import admin_security
from backend.models import AdminUser


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
    def __init__(self, target):
        self.target = target
        self.commits = 0
        self.rollbacks = 0

    def query(self, model):
        if model is AdminUser:
            return FakeQuery([self.target] if self.target is not None else [])
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

    def unexpected_revoke(*args, **kwargs):
        raise AssertionError("Rejected MFA disable must not mutate sessions")

    monkeypatch.setattr(admin_security, "set_totp_secret", unexpected_set)
    monkeypatch.setattr(admin_security, "revoke_admin_sessions", unexpected_revoke)

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


def test_nonproduction_totp_disable_revokes_all_existing_admin_sessions(monkeypatch):
    _patch_common(monkeypatch)
    monkeypatch.setattr(admin_security, "_production_admin_mfa_required", lambda: False)
    target = _target(active=True)
    db = TotpDb(target)
    revoked_admin_ids: list[int] = []

    monkeypatch.setattr(
        admin_security,
        "set_totp_secret",
        lambda db, admin_id, secret, enabled: SimpleNamespace(enabled=enabled),
    )
    monkeypatch.setattr(
        admin_security,
        "revoke_admin_sessions",
        lambda db, admin_id: revoked_admin_ids.append(admin_id) or 2,
    )

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
    assert revoked_admin_ids == [target.id]
    assert db.commits == 1
    assert db.rollbacks == 0


def test_production_inactive_admin_totp_disable_still_revokes_stale_sessions(monkeypatch):
    _patch_common(monkeypatch)
    monkeypatch.setattr(admin_security, "_production_admin_mfa_required", lambda: True)
    target = _target(active=False)
    db = TotpDb(target)
    revoked_admin_ids: list[int] = []

    monkeypatch.setattr(
        admin_security,
        "set_totp_secret",
        lambda db, admin_id, secret, enabled: SimpleNamespace(enabled=enabled),
    )
    monkeypatch.setattr(
        admin_security,
        "revoke_admin_sessions",
        lambda db, admin_id: revoked_admin_ids.append(admin_id) or 1,
    )

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
    assert revoked_admin_ids == [target.id]
    assert db.commits == 1
    assert db.rollbacks == 0
