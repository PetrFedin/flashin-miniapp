from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend import model_constraints  # noqa: F401
from backend.database import Base
from backend.models import DeliveryProvider
from backend.services import delivery_pricing


def _session():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _settings():
    return SimpleNamespace(
        default_delivery_price=100,
        courier_delivery_price=500,
        cdek_delivery_price=700,
        boxberry_delivery_price=650,
        pickup_delivery_price=0,
    )


def test_runtime_fallback_prices_are_exact(monkeypatch):
    db = _session()
    monkeypatch.setattr(delivery_pricing, "get_settings", _settings)

    assert delivery_pricing.calculate_delivery_price(db, "courier", "default")[-1].to_eng_string() == "500.00"
    assert delivery_pricing.calculate_delivery_price(db, "cdek", "default")[-1].to_eng_string() == "700.00"
    assert delivery_pricing.calculate_delivery_price(db, "boxberry", "default")[-1].to_eng_string() == "650.00"
    assert delivery_pricing.calculate_delivery_price(db, "pickup", "default")[-1].to_eng_string() == "0.00"


def test_provider_zone_override_wins_over_base_and_runtime(monkeypatch):
    db = _session()
    monkeypatch.setattr(delivery_pricing, "get_settings", _settings)
    db.add(
        DeliveryProvider(
            code="cdek",
            name="CDEK",
            active=True,
            config_json='{"base_price":720,"zones":{"far":950.55}}',
        )
    )
    db.commit()

    code, zone, far_price = delivery_pricing.calculate_delivery_price(db, " CDEK ", " FAR ")
    _, _, base_price = delivery_pricing.calculate_delivery_price(db, "cdek", "default")

    assert code == "cdek"
    assert zone == "far"
    assert far_price.to_eng_string() == "950.55"
    assert base_price.to_eng_string() == "720.00"


def test_active_custom_provider_uses_config_or_default(monkeypatch):
    db = _session()
    monkeypatch.setattr(delivery_pricing, "get_settings", _settings)
    provider = DeliveryProvider(
        code="custom",
        name="Custom",
        active=True,
        config_json='{"base_price":333.33}',
    )
    db.add(provider)
    db.commit()
    assert delivery_pricing.calculate_delivery_price(db, "custom", "default")[-1].to_eng_string() == "333.33"

    provider.config_json = "{}"
    db.commit()
    assert delivery_pricing.calculate_delivery_price(db, "custom", "default")[-1].to_eng_string() == "100.00"


def test_unknown_or_inactive_provider_is_rejected(monkeypatch):
    db = _session()
    monkeypatch.setattr(delivery_pricing, "get_settings", _settings)
    with pytest.raises(HTTPException) as unknown:
        delivery_pricing.calculate_delivery_price(db, "unknown", "default")
    assert unknown.value.status_code == 404

    db.add(
        DeliveryProvider(
            code="custom",
            name="Custom",
            active=False,
            config_json='{"base_price":100}',
        )
    )
    db.commit()
    with pytest.raises(HTTPException) as inactive:
        delivery_pricing.calculate_delivery_price(db, "custom", "default")
    assert inactive.value.status_code == 409


@pytest.mark.parametrize(
    "config_json",
    [
        "not-json",
        "[]",
        '{"zones":[]}',
        '{"base_price":"nan"}',
        '{"base_price":-1}',
        '{"base_price":10000001}',
    ],
)
def test_invalid_provider_pricing_configuration_fails_closed(monkeypatch, config_json):
    db = _session()
    monkeypatch.setattr(delivery_pricing, "get_settings", _settings)
    db.add(
        DeliveryProvider(
            code="custom",
            name="Custom",
            active=True,
            config_json=config_json,
        )
    )
    db.commit()

    with pytest.raises(HTTPException) as caught:
        delivery_pricing.calculate_delivery_price(db, "custom", "default")
    assert caught.value.status_code == 500


def test_invalid_zone_is_rejected_before_pricing(monkeypatch):
    db = _session()
    monkeypatch.setattr(delivery_pricing, "get_settings", _settings)
    with pytest.raises(HTTPException) as caught:
        delivery_pricing.calculate_delivery_price(db, "courier", "bad zone")
    assert caught.value.status_code == 400
