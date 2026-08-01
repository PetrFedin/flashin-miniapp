from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from backend.services import delivery


class FakeQuery:
    def __init__(self, zone=None):
        self.zone = zone

    def filter(self, *args, **kwargs):
        return self

    def first(self):
        return self.zone


class FakeSession:
    def __init__(self, zone=None):
        self.zone = zone
        self.queries = 0

    def query(self, _model):
        self.queries += 1
        return FakeQuery(self.zone)


def settings():
    return SimpleNamespace(
        courier_delivery_price=490.0,
        pickup_delivery_price=0.0,
        default_delivery_price=999.0,
    )


def test_active_zone_price_takes_precedence(monkeypatch):
    monkeypatch.setattr(delivery, "get_settings", settings)
    db = FakeSession(SimpleNamespace(price=250.0))

    assert delivery.calculate_delivery_price(db, " COURIER ") == 250.0
    assert db.queries == 1


def test_supported_types_use_explicit_fallbacks(monkeypatch):
    monkeypatch.setattr(delivery, "get_settings", settings)

    assert delivery.calculate_delivery_price(FakeSession(), "courier") == 490.0
    assert delivery.calculate_delivery_price(FakeSession(), "pickup") == 0.0


def test_unknown_type_never_uses_default_delivery_price(monkeypatch):
    monkeypatch.setattr(delivery, "get_settings", settings)
    db = FakeSession()

    with pytest.raises(HTTPException) as error:
        delivery.calculate_delivery_price(db, "drone")

    assert error.value.status_code == 400
    assert error.value.detail == "Unsupported delivery type"
    assert db.queries == 0
