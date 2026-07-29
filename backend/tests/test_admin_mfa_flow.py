import base64
import hashlib
import hmac
import struct

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend import admin_mfa_models  # noqa: F401
from backend.api.admin_auth import router
from backend.database import Base, get_db
from backend.models import AdminSession, AdminTotpSecret, AdminUser
from backend.security import hash_password
from backend.services import admin_security
from backend.services.admin_security import is_totp_secret_encrypted


def _totp_code(secret: str, at_time: int) -> str:
    normalized = secret.strip().upper()
    padding = "=" * ((8 - len(normalized) % 8) % 8)
    key = base64.b32decode(normalized + padding)
    counter = at_time // 30
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    index = digest[-1] & 0x0F
    value = struct.unpack(">I", digest[index : index + 4])[0] & 0x7FFFFFFF
    return f"{value % 1_000_000:06d}"


@pytest.fixture()
def admin_mfa_api(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    testing_session = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    Base.metadata.create_all(bind=engine)

    with testing_session() as db:
        admin = AdminUser(
            email="admin-mfa@test.local",
            password_hash=hash_password("Strong-Test-Password-2026!"),
            role="owner",
            active=True,
        )
        db.add(admin)
        db.commit()
        admin_id = admin.id

    app = FastAPI()
    app.include_router(router, prefix="/api")

    def override_get_db():
        with testing_session() as db:
            yield db

    app.dependency_overrides[get_db] = override_get_db
    clock = {"value": 1_800_000_000}
    monkeypatch.setattr(admin_security.time, "time", lambda: clock["value"])

    with TestClient(app) as client:
        yield client, testing_session, admin_id, clock

    Base.metadata.drop_all(bind=engine)
    engine.dispose()


def _login(client: TestClient, code: str | None = None):
    return client.post(
        "/api/admin/login",
        json={
            "email": "admin-mfa@test.local",
            "password": "Strong-Test-Password-2026!",
            "totp_code": code,
        },
    )


def test_admin_mfa_setup_and_replay_protection(admin_mfa_api):
    client, testing_session, admin_id, clock = admin_mfa_api

    initial_login = _login(client)
    assert initial_login.status_code == 200
    initial_payload = initial_login.json()
    assert initial_payload["access_token"] == ""
    assert initial_payload["mfa_setup_required"] is True
    setup_token = initial_payload["setup_token"]
    assert setup_token

    start = client.post(
        "/api/admin/mfa/setup/start",
        headers={"Authorization": f"Bearer {setup_token}"},
    )
    assert start.status_code == 200
    secret = start.json()["secret_once"]
    assert secret
    assert start.json()["otpauth_uri"].startswith("otpauth://totp/")

    first_code = _totp_code(secret, clock["value"])
    confirm = client.post(
        "/api/admin/mfa/setup/confirm",
        headers={"Authorization": f"Bearer {setup_token}"},
        json={"code": first_code},
    )
    assert confirm.status_code == 200
    assert confirm.json()["access_token"]

    replay_after_confirmation = _login(client, first_code)
    assert replay_after_confirmation.status_code == 401

    clock["value"] += 30
    second_code = _totp_code(secret, clock["value"])
    successful_login = _login(client, second_code)
    assert successful_login.status_code == 200
    assert successful_login.json()["access_token"]
    assert successful_login.json()["mfa_setup_required"] is False

    repeated_login = _login(client, second_code)
    assert repeated_login.status_code == 401

    with testing_session() as db:
        stored = db.query(AdminTotpSecret).filter(AdminTotpSecret.admin_id == admin_id).one()
        assert stored.enabled is True
        assert is_totp_secret_encrypted(stored.secret)
        replay_state = (
            db.query(admin_mfa_models.AdminTotpReplayState)
            .filter(admin_mfa_models.AdminTotpReplayState.admin_id == admin_id)
            .one()
        )
        assert replay_state.last_used_counter == clock["value"] // 30
        assert db.query(AdminSession).filter(AdminSession.admin_id == admin_id).count() == 2


def test_setup_token_cannot_access_normal_admin_session_flow(admin_mfa_api):
    client, _, _, _ = admin_mfa_api

    initial_login = _login(client)
    setup_token = initial_login.json()["setup_token"]

    response = client.post(
        "/api/admin/mfa/setup/confirm",
        headers={"Authorization": f"Bearer {setup_token}"},
        json={"code": "000000"},
    )

    assert response.status_code in {400, 409}
