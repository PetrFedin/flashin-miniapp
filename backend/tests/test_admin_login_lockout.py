from datetime import datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend import admin_mfa_models  # noqa: F401
from backend.api.admin_auth import router
from backend.config import get_settings
from backend.database import Base, get_db
from backend.models import AdminLoginEvent, AdminUser
from backend.security import hash_password
from backend.services.admin_login_lockout import admin_login_retry_after


@pytest.fixture()
def lockout_settings(monkeypatch):
    monkeypatch.setenv("ADMIN_LOGIN_MAX_FAILURES", "3")
    monkeypatch.setenv("ADMIN_LOGIN_FAILURE_WINDOW_MINUTES", "15")
    monkeypatch.setenv("ADMIN_LOGIN_LOCKOUT_MINUTES", "10")
    monkeypatch.setenv("ADMIN_MFA_REQUIRED", "false")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture()
def lockout_db(lockout_settings):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    testing_session = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    Base.metadata.create_all(bind=engine)
    try:
        yield testing_session
    finally:
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def _event(
    db,
    *,
    email: str,
    ip_address: str,
    success: bool,
    reason: str,
    created_at: datetime,
):
    db.add(
        AdminLoginEvent(
            email=email,
            success=success,
            reason=reason,
            ip_address=ip_address,
            user_agent="pytest",
            created_at=created_at,
        )
    )


def test_lockout_activates_at_failure_threshold(lockout_db):
    now = datetime(2026, 7, 29, 12, 0, 0)
    with lockout_db() as db:
        for offset in (3, 2, 1):
            _event(
                db,
                email="admin@test.local",
                ip_address="203.0.113.10",
                success=False,
                reason="invalid_credentials",
                created_at=now - timedelta(seconds=offset),
            )
        db.commit()

        retry_after = admin_login_retry_after(
            db,
            "ADMIN@test.local",
            "203.0.113.10",
            now=now,
        )

    assert 598 <= retry_after <= 600


def test_below_threshold_is_not_locked(lockout_db):
    now = datetime(2026, 7, 29, 12, 0, 0)
    with lockout_db() as db:
        for offset in (2, 1):
            _event(
                db,
                email="admin@test.local",
                ip_address="203.0.113.10",
                success=False,
                reason="invalid_credentials",
                created_at=now - timedelta(seconds=offset),
            )
        db.commit()

        retry_after = admin_login_retry_after(
            db,
            "admin@test.local",
            "203.0.113.10",
            now=now,
        )

    assert retry_after == 0


def test_source_ip_lockout_applies_across_different_emails(lockout_db):
    now = datetime(2026, 7, 29, 12, 0, 0)
    with lockout_db() as db:
        for index, email in enumerate(("one@test.local", "two@test.local", "three@test.local"), start=1):
            _event(
                db,
                email=email,
                ip_address="198.51.100.20",
                success=False,
                reason="invalid_credentials",
                created_at=now - timedelta(seconds=4 - index),
            )
        db.commit()

        retry_after = admin_login_retry_after(
            db,
            "different@test.local",
            "198.51.100.20",
            now=now,
        )

    assert retry_after > 0


def test_successful_authentication_resets_email_failure_sequence(lockout_db):
    now = datetime(2026, 7, 29, 12, 0, 0)
    with lockout_db() as db:
        for offset in (10, 9, 8):
            _event(
                db,
                email="admin@test.local",
                ip_address="203.0.113.10",
                success=False,
                reason="invalid_credentials",
                created_at=now - timedelta(seconds=offset),
            )
        _event(
            db,
            email="admin@test.local",
            ip_address="203.0.113.11",
            success=True,
            reason="success",
            created_at=now - timedelta(seconds=7),
        )
        for offset in (2, 1):
            _event(
                db,
                email="admin@test.local",
                ip_address="203.0.113.12",
                success=False,
                reason="invalid_or_replayed_totp",
                created_at=now - timedelta(seconds=offset),
            )
        db.commit()

        retry_after = admin_login_retry_after(
            db,
            "admin@test.local",
            "203.0.113.99",
            now=now,
        )

    assert retry_after == 0


def test_lockout_expires_without_extending_on_blocked_requests(lockout_db):
    now = datetime(2026, 7, 29, 12, 0, 0)
    with lockout_db() as db:
        for minutes in (12, 11, 10):
            _event(
                db,
                email="admin@test.local",
                ip_address="203.0.113.10",
                success=False,
                reason="invalid_credentials",
                created_at=now - timedelta(minutes=minutes),
            )
        _event(
            db,
            email="admin@test.local",
            ip_address="203.0.113.10",
            success=False,
            reason="login_locked",
            created_at=now - timedelta(seconds=30),
        )
        db.commit()

        retry_after = admin_login_retry_after(
            db,
            "admin@test.local",
            "203.0.113.10",
            now=now,
        )

    assert retry_after == 0


def test_login_endpoint_returns_429_with_retry_after(lockout_db):
    with lockout_db() as db:
        db.add(
            AdminUser(
                email="locked-admin@test.local",
                password_hash=hash_password("Strong-Lockout-Password-2026!"),
                role="owner",
                active=True,
            )
        )
        db.commit()

    app = FastAPI()
    app.include_router(router, prefix="/api")

    def override_get_db():
        with lockout_db() as db:
            yield db

    app.dependency_overrides[get_db] = override_get_db

    with TestClient(app) as client:
        for _ in range(3):
            failed = client.post(
                "/api/admin/login",
                json={
                    "email": "locked-admin@test.local",
                    "password": "wrong-password",
                },
            )
            assert failed.status_code == 401

        locked = client.post(
            "/api/admin/login",
            json={
                "email": "locked-admin@test.local",
                "password": "Strong-Lockout-Password-2026!",
            },
        )

    assert locked.status_code == 429
    assert locked.json()["detail"] == "Too many admin login attempts"
    assert int(locked.headers["retry-after"]) > 0
