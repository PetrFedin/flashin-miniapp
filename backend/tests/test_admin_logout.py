from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from backend.api import admin_auth
from backend.models import AdminSession


class _Query:
    def __init__(self, row):
        self.row = row
        self.locked = False

    def filter(self, *args, **kwargs):
        return self

    def with_for_update(self):
        self.locked = True
        return self

    def first(self):
        return self.row


class _Db:
    def __init__(self, session):
        self.session = session
        self.query_obj = None
        self.commits = 0
        self.rollbacks = 0

    def query(self, model):
        assert model is AdminSession
        self.query_obj = _Query(self.session)
        return self.query_obj

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


def _credentials(token="token-one", scheme="Bearer"):
    return SimpleNamespace(credentials=token, scheme=scheme)


def _admin():
    return SimpleNamespace(id=17, email="owner@flashin.store")


def test_logout_revokes_only_selected_session_and_returns_no_store(monkeypatch):
    current = SimpleNamespace(
        admin_id=17,
        session_token_hash="hash-one",
        revoked=False,
        revoked_at=None,
        ip_address="203.0.113.7",
        user_agent="pytest-current-session",
    )
    other = SimpleNamespace(
        admin_id=17,
        session_token_hash="hash-two",
        revoked=False,
        revoked_at=None,
        ip_address="203.0.113.8",
        user_agent="pytest-other-session",
    )
    db = _Db(current)
    events = []
    monkeypatch.setattr(admin_auth, "sha256", lambda token: f"hash-{token.split('-')[-1]}")
    monkeypatch.setattr(
        admin_auth,
        "log_admin_login",
        lambda db, email, admin_id, success, reason, ip, user_agent: events.append(
            (email, admin_id, success, reason, ip, user_agent)
        ),
    )

    response = admin_auth.admin_session_logout(
        credentials=_credentials(),
        admin=_admin(),
        db=db,
    )

    assert response.status_code == 204
    assert response.headers["cache-control"] == "no-store, max-age=0"
    assert response.headers["pragma"] == "no-cache"
    assert current.revoked is True
    assert current.revoked_at is not None
    assert other.revoked is False
    assert other.revoked_at is None
    assert db.query_obj is not None and db.query_obj.locked is True
    assert db.commits == 1
    assert db.rollbacks == 0
    assert events == [
        (
            "owner@flashin.store",
            17,
            True,
            "logout",
            "203.0.113.7",
            "pytest-current-session",
        )
    ]


def test_logout_missing_active_session_fails_closed(monkeypatch):
    db = _Db(None)
    events = []
    monkeypatch.setattr(admin_auth, "sha256", lambda token: "hash-one")
    monkeypatch.setattr(admin_auth, "log_admin_login", lambda *args, **kwargs: events.append(args))

    with pytest.raises(HTTPException) as exc_info:
        admin_auth.admin_session_logout(
            credentials=_credentials(),
            admin=_admin(),
            db=db,
        )

    assert exc_info.value.status_code == 401
    assert db.commits == 0
    assert db.rollbacks == 1
    assert events == []


def test_logout_requires_bearer_credentials():
    db = _Db(None)

    with pytest.raises(HTTPException) as exc_info:
        admin_auth.admin_session_logout(
            credentials=_credentials(scheme="Basic"),
            admin=_admin(),
            db=db,
        )

    assert exc_info.value.status_code == 401
    assert db.query_obj is None
    assert db.commits == 0
    assert db.rollbacks == 0


def test_logout_route_is_bound_to_exact_current_bearer_session():
    source = (
        Path(__file__).resolve().parents[1] / "api" / "admin_auth.py"
    ).read_text(encoding="utf-8")
    endpoint = source.split('@router.post("/logout", status_code=204)', 1)[1].split(
        '@router.post("/password-reset/confirm")', 1
    )[0]

    assert "Depends(bearer)" in endpoint
    assert "Depends(get_current_admin)" in endpoint
    assert "AdminSession.admin_id == admin.id" in endpoint
    assert "AdminSession.session_token_hash == sha256(credentials.credentials)" in endpoint
    assert "AdminSession.revoked.is_(False)" in endpoint
    assert ".with_for_update()" in endpoint
    assert 'session.revoked = True' in endpoint
    assert 'session.revoked_at = utcnow_naive()' in endpoint
    assert '"logout"' in endpoint
    assert "revoke_admin_sessions(" not in endpoint
