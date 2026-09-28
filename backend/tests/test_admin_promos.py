from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from backend.api import admin_promos
from backend.main import app
from backend.schemas import PromoCodeCreate


class FakeSession:
    def __init__(self):
        self.added = []
        self.commits = 0
        self.rollbacks = 0
        self.flushes = 0

    def add(self, value):
        self.added.append(value)

    def flush(self):
        self.flushes += 1
        if self.added:
            self.added[-1].id = 91

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


def payload(**overrides):
    values = {
        "code": " launch-25 ",
        "discount_type": " percent ",
        "discount_value": 25,
        "min_amount": 1000,
        "max_uses": 50,
        "active": True,
        "expires_at": None,
    }
    values.update(overrides)
    return PromoCodeCreate(**values)


def test_valid_admin_promo_is_normalized_before_persistence(monkeypatch):
    db = FakeSession()
    audit_calls = []
    monkeypatch.setattr(admin_promos, "require_permission", lambda *_args: None)
    monkeypatch.setattr(
        admin_promos,
        "log_admin_action",
        lambda *args: audit_calls.append(args),
    )

    result = admin_promos.admin_create_promo(
        payload(),
        admin=SimpleNamespace(id=5),
        db=db,
    )

    assert result == {"ok": True, "id": 91}
    assert len(db.added) == 1
    promo = db.added[0]
    assert promo.code == "LAUNCH-25"
    assert promo.discount_type == "percent"
    assert promo.discount_value == 25.0
    assert promo.min_amount == 1000.0
    assert promo.max_uses == 50
    assert db.flushes == 1
    assert db.commits == 1
    assert db.rollbacks == 0
    assert len(audit_calls) == 1


def test_invalid_admin_promo_is_rejected_before_database_write(monkeypatch):
    db = FakeSession()
    monkeypatch.setattr(admin_promos, "require_permission", lambda *_args: None)

    with pytest.raises(HTTPException) as error:
        admin_promos.admin_create_promo(
            payload(discount_value=150),
            admin=SimpleNamespace(id=5),
            db=db,
        )

    assert error.value.status_code == 400
    assert error.value.detail == "Percent discount cannot exceed 100"
    assert db.added == []
    assert db.flushes == 0
    assert db.commits == 0
    assert db.rollbacks == 0


def test_application_exposes_one_hardened_admin_promo_route():
    path = "/api/admin/promocodes"
    assert str(app.url_path_for("admin_create_promo")) == path
    operations = app.openapi()["paths"][path]
    assert "post" in operations
    assert operations["post"]["operationId"].startswith("admin_create_promo")
