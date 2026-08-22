from types import SimpleNamespace

import pytest

from backend.models import AdminTotpSecret, AdminUser
from scripts import provision_admin_totp as bootstrap


class FakeQuery:
    def __init__(self, values):
        self.values = list(values)

    def order_by(self, *args, **kwargs):
        return self

    def filter(self, *args, **kwargs):
        return self

    def with_for_update(self):
        return self

    def all(self):
        return list(self.values)


class BootstrapDb:
    def __init__(self, admins, enabled_totp=()):
        self.admins = list(admins)
        self.enabled_totp = list(enabled_totp)

    def query(self, model):
        if model is AdminUser:
            return FakeQuery(self.admins)
        if model is AdminTotpSecret:
            return FakeQuery(self.enabled_totp)
        raise AssertionError(f"Unexpected query model: {model}")


def _production(monkeypatch):
    monkeypatch.setattr(
        bootstrap,
        "get_settings",
        lambda: SimpleNamespace(app_env="production", admin_email="owner@flashin.test"),
    )


def test_bootstrap_first_admin_consumes_enrollment_counter_and_audits(monkeypatch):
    _production(monkeypatch)
    admin = SimpleNamespace(id=17, email="owner@flashin.test", active=True)
    db = BootstrapDb([admin])
    calls = []

    monkeypatch.setattr(bootstrap, "match_totp_counter", lambda secret, code: 555)
    monkeypatch.setattr(
        bootstrap,
        "set_totp_secret",
        lambda db, admin_id, secret, enabled: calls.append(
            ("set", admin_id, secret, enabled)
        ),
    )
    monkeypatch.setattr(
        bootstrap,
        "consume_totp_counter",
        lambda db, admin_id, counter: calls.append(("consume", admin_id, counter)) or True,
    )
    monkeypatch.setattr(
        bootstrap,
        "log_admin_action",
        lambda db, actor, action, entity_type, entity_id, payload: calls.append(
            ("audit", actor, action, entity_type, entity_id, payload)
        ),
    )

    result = bootstrap.bootstrap_first_admin_totp(
        db,
        email="OWNER@FLASHIN.TEST",
        secret="JBSWY3DPEHPK3PXP",
        verification_code="123456",
    )

    assert result is admin
    assert calls == [
        ("set", 17, "JBSWY3DPEHPK3PXP", True),
        ("consume", 17, 555),
        (
            "audit",
            None,
            "admin.totp.bootstrap",
            "admin_user",
            17,
            {"enabled": True, "operator_path": "offline_first_admin"},
        ),
    ]


def test_bootstrap_refuses_when_active_admin_already_has_mfa(monkeypatch):
    _production(monkeypatch)
    admin = SimpleNamespace(id=17, email="owner@flashin.test", active=True)
    existing = SimpleNamespace(admin_id=17, enabled=True)
    db = BootstrapDb([admin], [existing])
    monkeypatch.setattr(
        bootstrap,
        "set_totp_secret",
        lambda *args, **kwargs: pytest.fail("must not mutate existing MFA"),
    )

    with pytest.raises(bootstrap.BootstrapError, match="already has MFA"):
        bootstrap.bootstrap_first_admin_totp(
            db,
            email=admin.email,
            secret="JBSWY3DPEHPK3PXP",
            verification_code="123456",
        )


def test_bootstrap_refuses_missing_or_inactive_admin(monkeypatch):
    _production(monkeypatch)
    inactive = SimpleNamespace(id=17, email="owner@flashin.test", active=False)

    with pytest.raises(bootstrap.BootstrapError, match="does not exist"):
        bootstrap.bootstrap_first_admin_totp(
            BootstrapDb([]),
            email="owner@flashin.test",
            secret="JBSWY3DPEHPK3PXP",
            verification_code="123456",
        )

    with pytest.raises(bootstrap.BootstrapError, match="inactive"):
        bootstrap.bootstrap_first_admin_totp(
            BootstrapDb([inactive]),
            email=inactive.email,
            secret="JBSWY3DPEHPK3PXP",
            verification_code="123456",
        )


def test_bootstrap_refuses_invalid_or_unconsumable_totp(monkeypatch):
    _production(monkeypatch)
    admin = SimpleNamespace(id=17, email="owner@flashin.test", active=True)
    db = BootstrapDb([admin])

    monkeypatch.setattr(bootstrap, "match_totp_counter", lambda *args, **kwargs: None)
    with pytest.raises(bootstrap.BootstrapError, match="invalid"):
        bootstrap.bootstrap_first_admin_totp(
            db,
            email=admin.email,
            secret="JBSWY3DPEHPK3PXP",
            verification_code="000000",
        )

    monkeypatch.setattr(bootstrap, "match_totp_counter", lambda *args, **kwargs: 555)
    monkeypatch.setattr(bootstrap, "set_totp_secret", lambda *args, **kwargs: None)
    monkeypatch.setattr(bootstrap, "consume_totp_counter", lambda *args, **kwargs: False)
    with pytest.raises(bootstrap.BootstrapError, match="could not be consumed"):
        bootstrap.bootstrap_first_admin_totp(
            db,
            email=admin.email,
            secret="JBSWY3DPEHPK3PXP",
            verification_code="123456",
        )


def test_bootstrap_is_production_only_and_has_no_secret_cli_options(monkeypatch):
    monkeypatch.setattr(
        bootstrap,
        "get_settings",
        lambda: SimpleNamespace(app_env="development", admin_email="owner@flashin.test"),
    )
    admin = SimpleNamespace(id=17, email="owner@flashin.test", active=True)
    with pytest.raises(bootstrap.BootstrapError, match="production-only"):
        bootstrap.bootstrap_first_admin_totp(
            BootstrapDb([admin]),
            email=admin.email,
            secret="JBSWY3DPEHPK3PXP",
            verification_code="123456",
        )

    option_strings = {
        option
        for action in bootstrap.build_parser()._actions
        for option in action.option_strings
    }
    assert "--secret" not in option_strings
    assert "--verification-code" not in option_strings
    assert bootstrap.ACK_FLAG in option_strings
