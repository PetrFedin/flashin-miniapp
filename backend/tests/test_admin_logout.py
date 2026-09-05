import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.api.admin_auth import router
from backend.database import Base, get_db
from backend.models import AdminLoginEvent, AdminSession, AdminUser
from backend.security import create_admin_token, get_current_admin, hash_password
from backend.services.admin_security import create_admin_session, sha256


@pytest.fixture()
def admin_logout_api():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    testing_session = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    Base.metadata.create_all(bind=engine)

    with testing_session() as db:
        admin = AdminUser(
            email="logout-admin@test.local",
            password_hash=hash_password("Strong-Logout-Password-2026!"),
            role="owner",
            active=True,
        )
        db.add(admin)
        db.flush()

        first_token = create_admin_token(admin.id, admin.role)
        second_token = create_admin_token(admin.id, admin.role)
        assert first_token != second_token

        create_admin_session(
            db,
            admin.id,
            first_token,
            ip="127.0.0.1",
            user_agent="pytest-first-session",
        )
        create_admin_session(
            db,
            admin.id,
            second_token,
            ip="127.0.0.2",
            user_agent="pytest-second-session",
        )
        db.commit()
        admin_id = admin.id

    app = FastAPI()
    app.include_router(router, prefix="/api")

    @app.get("/api/admin/protected")
    def protected(admin: AdminUser = Depends(get_current_admin)):
        return {"admin_id": admin.id}

    def override_get_db():
        with testing_session() as db:
            yield db

    app.dependency_overrides[get_db] = override_get_db

    with TestClient(app) as client:
        yield client, testing_session, admin_id, first_token, second_token

    Base.metadata.drop_all(bind=engine)
    engine.dispose()


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_logout_revokes_only_current_bearer_session(admin_logout_api):
    client, testing_session, admin_id, first_token, second_token = admin_logout_api

    assert client.get("/api/admin/protected", headers=_headers(first_token)).status_code == 200
    assert client.get("/api/admin/protected", headers=_headers(second_token)).status_code == 200

    response = client.post("/api/admin/logout", headers=_headers(first_token))

    assert response.status_code == 204
    assert response.headers["cache-control"] == "no-store, max-age=0"
    assert response.headers["pragma"] == "no-cache"
    assert client.get("/api/admin/protected", headers=_headers(first_token)).status_code == 401

    still_active = client.get("/api/admin/protected", headers=_headers(second_token))
    assert still_active.status_code == 200
    assert still_active.json() == {"admin_id": admin_id}

    with testing_session() as db:
        sessions = (
            db.query(AdminSession)
            .filter(AdminSession.admin_id == admin_id)
            .order_by(AdminSession.id.asc())
            .all()
        )
        assert len(sessions) == 2
        by_hash = {session.session_token_hash: session for session in sessions}
        first_session = by_hash[sha256(first_token)]
        second_session = by_hash[sha256(second_token)]
        assert first_session.revoked is True
        assert first_session.revoked_at is not None
        assert second_session.revoked is False
        assert second_session.revoked_at is None

        events = (
            db.query(AdminLoginEvent)
            .filter(
                AdminLoginEvent.admin_id == admin_id,
                AdminLoginEvent.reason == "logout",
            )
            .all()
        )
        assert len(events) == 1
        assert events[0].success is True
        assert events[0].ip_address == "127.0.0.1"
        assert events[0].user_agent == "pytest-first-session"


def test_revoked_session_cannot_logout_twice(admin_logout_api):
    client, _, _, first_token, second_token = admin_logout_api

    first = client.post("/api/admin/logout", headers=_headers(first_token))
    second = client.post("/api/admin/logout", headers=_headers(first_token))

    assert first.status_code == 204
    assert second.status_code == 401
    assert client.get("/api/admin/protected", headers=_headers(second_token)).status_code == 200


def test_logout_requires_active_registered_bearer(admin_logout_api):
    client, _, _, _, _ = admin_logout_api

    assert client.post("/api/admin/logout").status_code == 401
    assert client.post(
        "/api/admin/logout",
        headers={"Authorization": "Bearer not-a-registered-admin-session"},
    ).status_code == 401
