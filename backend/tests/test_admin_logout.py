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
from backend.services.admin_security import create_admin_session


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
        token = create_admin_token(admin.id, admin.role)
        create_admin_session(
            db,
            admin.id,
            token,
            ip="127.0.0.1",
            user_agent="pytest",
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
        yield client, testing_session, admin_id, token

    Base.metadata.drop_all(bind=engine)
    engine.dispose()


def test_logout_revokes_only_the_current_admin_session(admin_logout_api):
    client, testing_session, admin_id, token = admin_logout_api
    headers = {"Authorization": f"Bearer {token}"}

    before = client.get("/api/admin/protected", headers=headers)
    logout = client.post("/api/admin/logout", headers=headers)
    after = client.get("/api/admin/protected", headers=headers)

    assert before.status_code == 200
    assert before.json() == {"admin_id": admin_id}
    assert logout.status_code == 204
    assert logout.headers["cache-control"] == "no-store"
    assert after.status_code == 401

    with testing_session() as db:
        session = (
            db.query(AdminSession)
            .filter(AdminSession.admin_id == admin_id)
            .one()
        )
        assert session.revoked is True
        assert session.revoked_at is not None
        event = (
            db.query(AdminLoginEvent)
            .filter(
                AdminLoginEvent.admin_id == admin_id,
                AdminLoginEvent.reason == "logout",
            )
            .one()
        )
        assert event.success is True


def test_revoked_session_cannot_logout_twice(admin_logout_api):
    client, _, _, token = admin_logout_api
    headers = {"Authorization": f"Bearer {token}"}

    first = client.post("/api/admin/logout", headers=headers)
    second = client.post("/api/admin/logout", headers=headers)

    assert first.status_code == 204
    assert second.status_code == 401
