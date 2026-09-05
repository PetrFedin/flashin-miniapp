import base64
import hashlib
import hmac
import struct
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from backend.admin_mfa_models import AdminTotpReplayState
from backend.api import admin_auth
from backend.api import admin_security as admin_security_api
from backend.models import AdminTotpSecret, AdminUser
from backend.services.admin_security import (
    consume_totp_counter,
    match_totp_counter,
    reset_totp_replay_state,
    verify_totp,
)


SECRET = "JBSWY3DPEHPK3PXP"


def _totp_code(secret: str, counter: int) -> str:
    key = base64.b32decode(secret, casefold=True)
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    index = digest[-1] & 0x0F
    value = struct.unpack(">I", digest[index : index + 4])[0] & 0x7FFFFFFF
    return f"{value % 1_000_000:06d}"


class FakeQuery:
    def __init__(self, values):
        self.values = values

    def filter(self, *args, **kwargs):
        return self

    def with_for_update(self):
        return self

    def first(self):
        return self.values[0] if self.values else None


class ReplayDb:
    def __init__(self, state=None):
        self.state = state
        self.deleted = []

    def query(self, model):
        if model is AdminTotpReplayState:
            return FakeQuery([self.state] if self.state is not None else [])
        raise AssertionError(f"Unexpected model query: {model}")

    def add(self, row):
        assert isinstance(row, AdminTotpReplayState)
        self.state = row

    def delete(self, row):
        self.deleted.append(row)
        if row is self.state:
            self.state = None


class LoginDb:
    def __init__(self, admin, totp):
        self.admin = admin
        self.totp = totp
        self.replay_state = None
        self.commits = 0
        self.rollbacks = 0

    def query(self, model):
        if model is AdminUser:
            return FakeQuery([self.admin])
        if model is AdminTotpSecret:
            return FakeQuery([self.totp])
        if model is AdminTotpReplayState:
            return FakeQuery([self.replay_state] if self.replay_state is not None else [])
        raise AssertionError(f"Unexpected model query: {model}")

    def add(self, row):
        if isinstance(row, AdminTotpReplayState):
            self.replay_state = row
            return
        raise AssertionError(f"Unexpected row add: {type(row)!r}")

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


class ConfigureDb:
    def __init__(self, target):
        self.target = target
        self.commits = 0
        self.rollbacks = 0

    def query(self, model):
        if model is AdminUser:
            return FakeQuery([self.target])
        raise AssertionError(f"Unexpected model query: {model}")

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


def test_match_totp_counter_returns_exact_window_counter():
    current_counter = 2_000_000
    code = _totp_code(SECRET, current_counter - 1)

    assert match_totp_counter(
        SECRET,
        code,
        at_time=current_counter * 30,
        window=1,
    ) == current_counter - 1
    assert verify_totp(
        SECRET,
        code,
        at_time=current_counter * 30,
        window=1,
    ) is True
    assert match_totp_counter(
        SECRET,
        code,
        at_time=current_counter * 30,
        window=0,
    ) is None


def test_consume_totp_counter_is_strictly_monotonic():
    db = ReplayDb()

    assert consume_totp_counter(db, 17, 100) is True
    assert db.state is not None
    assert db.state.last_used_counter == 100

    assert consume_totp_counter(db, 17, 100) is False
    assert consume_totp_counter(db, 17, 99) is False
    assert db.state.last_used_counter == 100

    assert consume_totp_counter(db, 17, 101) is True
    assert db.state.last_used_counter == 101


def test_reset_totp_replay_state_removes_old_counter():
    state = AdminTotpReplayState(admin_id=17, last_used_counter=123)
    db = ReplayDb(state)

    reset_totp_replay_state(db, 17)

    assert db.state is None
    assert db.deleted == [state]


def test_admin_login_rejects_second_use_of_same_totp_counter(monkeypatch):
    admin = SimpleNamespace(
        id=17,
        email="owner@flashin.test",
        password_hash="stored-hash",
        role="owner",
        active=True,
    )
    totp = SimpleNamespace(admin_id=17, secret="encrypted-secret", enabled=True)
    db = LoginDb(admin, totp)
    reasons: list[str] = []

    monkeypatch.setattr(admin_auth, "_request_identity", lambda request: ("127.0.0.1", "pytest"))
    monkeypatch.setattr(admin_auth, "is_admin_ip_allowed", lambda db, ip: True)
    monkeypatch.setattr(admin_auth, "verify_password", lambda password, password_hash: True)
    monkeypatch.setattr(admin_auth, "password_needs_rehash", lambda password_hash: False)
    monkeypatch.setattr(admin_auth, "_production_admin_mfa_required", lambda: True)
    monkeypatch.setattr(admin_auth, "match_stored_totp_counter", lambda *args, **kwargs: 123)
    monkeypatch.setattr(admin_auth, "upgrade_totp_secret_encryption", lambda row: False)
    monkeypatch.setattr(admin_auth, "create_admin_token", lambda *args: "admin-token")
    monkeypatch.setattr(admin_auth, "create_admin_session", lambda *args: None)
    monkeypatch.setattr(
        admin_auth,
        "log_admin_login",
        lambda db, email, admin_id, success, reason, ip, user_agent: reasons.append(reason),
    )

    payload = admin_auth.AdminSessionLoginIn(
        email=admin.email,
        password="CorrectHorseBatteryStaple!",
        totp_code="123456",
    )
    first = admin_auth.admin_session_login(payload, request=None, db=db)
    assert first.access_token == "admin-token"
    assert db.replay_state is not None
    assert db.replay_state.last_used_counter == 123

    with pytest.raises(HTTPException) as exc_info:
        admin_auth.admin_session_login(payload, request=None, db=db)

    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == "Invalid admin credentials"
    assert reasons == ["success", "totp_replay"]
    assert db.replay_state.last_used_counter == 123


def test_admin_login_accepts_newer_totp_counter_after_previous_use(monkeypatch):
    admin = SimpleNamespace(
        id=17,
        email="owner@flashin.test",
        password_hash="stored-hash",
        role="owner",
        active=True,
    )
    totp = SimpleNamespace(admin_id=17, secret="encrypted-secret", enabled=True)
    db = LoginDb(admin, totp)
    counters = iter([123, 124])

    monkeypatch.setattr(admin_auth, "_request_identity", lambda request: ("127.0.0.1", "pytest"))
    monkeypatch.setattr(admin_auth, "is_admin_ip_allowed", lambda db, ip: True)
    monkeypatch.setattr(admin_auth, "verify_password", lambda password, password_hash: True)
    monkeypatch.setattr(admin_auth, "password_needs_rehash", lambda password_hash: False)
    monkeypatch.setattr(admin_auth, "_production_admin_mfa_required", lambda: True)
    monkeypatch.setattr(admin_auth, "match_stored_totp_counter", lambda *args, **kwargs: next(counters))
    monkeypatch.setattr(admin_auth, "upgrade_totp_secret_encryption", lambda row: False)
    monkeypatch.setattr(admin_auth, "create_admin_token", lambda *args: "admin-token")
    monkeypatch.setattr(admin_auth, "create_admin_session", lambda *args: None)
    monkeypatch.setattr(admin_auth, "log_admin_login", lambda *args, **kwargs: None)

    payload = admin_auth.AdminSessionLoginIn(
        email=admin.email,
        password="CorrectHorseBatteryStaple!",
        totp_code="123456",
    )
    assert admin_auth.admin_session_login(payload, request=None, db=db).access_token == "admin-token"
    assert admin_auth.admin_session_login(payload, request=None, db=db).access_token == "admin-token"
    assert db.replay_state.last_used_counter == 124


def test_totp_enrollment_consumes_the_verification_counter(monkeypatch):
    target = SimpleNamespace(id=17)
    acting_admin = SimpleNamespace(id=1)
    db = ConfigureDb(target)
    consumed: list[tuple[int, int]] = []

    monkeypatch.setattr(admin_security_api, "require_permission", lambda *args, **kwargs: None)
    monkeypatch.setattr(admin_security_api, "match_totp_counter", lambda *args, **kwargs: 555)
    monkeypatch.setattr(
        admin_security_api,
        "set_totp_secret",
        lambda db, admin_id, secret, enabled: SimpleNamespace(enabled=enabled),
    )
    monkeypatch.setattr(
        admin_security_api,
        "consume_totp_counter",
        lambda db, admin_id, counter: consumed.append((admin_id, counter)) or True,
    )
    monkeypatch.setattr(admin_security_api, "log_admin_action", lambda *args, **kwargs: None)

    result = admin_security_api.configure_totp(
        17,
        admin_security_api.AdminTotpIn(
            secret=SECRET,
            enabled=True,
            verification_code="123456",
        ),
        admin=acting_admin,
        db=db,
    )

    assert result == {"ok": True, "enabled": True}
    assert consumed == [(17, 555)]
    assert db.commits == 1
    assert db.rollbacks == 0


def test_totp_enrollment_fails_closed_when_counter_cannot_be_consumed(monkeypatch):
    target = SimpleNamespace(id=17)
    acting_admin = SimpleNamespace(id=1)
    db = ConfigureDb(target)

    monkeypatch.setattr(admin_security_api, "require_permission", lambda *args, **kwargs: None)
    monkeypatch.setattr(admin_security_api, "match_totp_counter", lambda *args, **kwargs: 555)
    monkeypatch.setattr(
        admin_security_api,
        "set_totp_secret",
        lambda db, admin_id, secret, enabled: SimpleNamespace(enabled=enabled),
    )
    monkeypatch.setattr(admin_security_api, "consume_totp_counter", lambda *args, **kwargs: False)
    monkeypatch.setattr(admin_security_api, "log_admin_action", lambda *args, **kwargs: None)

    with pytest.raises(HTTPException) as exc_info:
        admin_security_api.configure_totp(
            17,
            admin_security_api.AdminTotpIn(
                secret=SECRET,
                enabled=True,
                verification_code="123456",
            ),
            admin=acting_admin,
            db=db,
        )

    assert exc_info.value.status_code == 409
    assert db.commits == 0
    assert db.rollbacks == 1
