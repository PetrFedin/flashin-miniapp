from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool


@pytest.fixture()
def webhook_api(monkeypatch):
    from backend.api import payments, telegram_webhook
    from backend.database import Base, get_db
    from backend import models  # noqa: F401
    from backend import telegram_commerce_models  # noqa: F401

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    testing_session = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    Base.metadata.create_all(bind=engine)

    app = FastAPI()
    app.include_router(telegram_webhook.router, prefix="/api")
    app.include_router(payments.router, prefix="/api")

    def override_get_db():
        with testing_session() as db:
            yield db

    app.dependency_overrides[get_db] = override_get_db
    monkeypatch.setattr(
        telegram_webhook,
        "settings",
        SimpleNamespace(telegram_webhook_secret="expected", telegram_bot_token="test-token"),
    )

    with TestClient(app) as client:
        yield client, testing_session, payments

    Base.metadata.drop_all(bind=engine)
    engine.dispose()


def test_telegram_webhook_rejects_missing_configured_secret(monkeypatch):
    from backend.api import telegram_webhook

    monkeypatch.setattr(telegram_webhook, "settings", SimpleNamespace(telegram_webhook_secret=""))

    with pytest.raises(HTTPException) as exc:
        telegram_webhook._validate_secret(None)

    assert exc.value.status_code == 401


def test_telegram_webhook_rejects_incorrect_secret(monkeypatch):
    from backend.api import telegram_webhook

    monkeypatch.setattr(telegram_webhook, "settings", SimpleNamespace(telegram_webhook_secret="expected"))

    with pytest.raises(HTTPException) as exc:
        telegram_webhook._validate_secret("incorrect")

    assert exc.value.status_code == 401


def test_telegram_webhook_accepts_matching_secret(monkeypatch):
    from backend.api import telegram_webhook

    monkeypatch.setattr(telegram_webhook, "settings", SimpleNamespace(telegram_webhook_secret="expected"))

    telegram_webhook._validate_secret("expected")


def test_production_settings_require_non_placeholder_webhook_secret():
    from backend.config import Settings

    with pytest.raises(ValidationError, match="TELEGRAM_WEBHOOK_SECRET is required"):
        Settings(
            app_env="production",
            telegram_bot_token="test-token",
            telegram_webhook_secret="",
            jwt_secret="test-secret",
            _env_file=None,
        )


def test_development_settings_preserve_empty_webhook_secret_compatibility():
    from backend.config import Settings

    settings = Settings(
        app_env="development",
        telegram_bot_token="test-token",
        telegram_webhook_secret="",
        jwt_secret="test-secret",
        _env_file=None,
    )

    assert settings.telegram_webhook_secret == ""


def test_telegram_webhook_endpoint_rejects_fabricated_payment_without_secret(webhook_api):
    from backend.models import Customer
    from backend.telegram_commerce_models import TelegramOffer, TelegramPurchase

    client, testing_session, _ = webhook_api
    with testing_session() as db:
        customer = Customer(telegram_id="telegram-1")
        offer = TelegramOffer(code="DROP", title="Drop", offer_type="drop", stars_amount=100)
        db.add_all([customer, offer])
        db.flush()
        purchase = TelegramPurchase(
            customer_id=customer.id,
            offer_id=offer.id,
            invoice_payload="invoice-1",
            stars_amount=100,
        )
        db.add(purchase)
        db.commit()
        purchase_id = purchase.id

    response = client.post(
        "/api/telegram/webhook",
        json={
            "message": {
                "successful_payment": {
                    "invoice_payload": "invoice-1",
                    "currency": "XTR",
                    "total_amount": 100,
                    "telegram_payment_charge_id": "forged-charge",
                }
            }
        },
    )

    assert response.status_code == 401
    with testing_session() as db:
        assert db.get(TelegramPurchase, purchase_id).status == "invoice_created"


def test_telegram_webhook_endpoint_accepts_official_secret_header(webhook_api):
    client, _, _ = webhook_api

    response = client.post(
        "/api/telegram/webhook",
        headers={"X-Telegram-Bot-Api-Secret-Token": "expected"},
        json={},
    )

    assert response.status_code == 200
    assert response.json() == {"ok": True, "handled": "ignored"}


def _payment_binding_data():
    payment = SimpleNamespace(order_id=7, provider_payment_id="payment-123")
    order = SimpleNamespace(id=7, total_amount=1299.0, currency="RUB")
    provider_payment = {
        "id": "payment-123",
        "metadata": {"order_id": "7"},
        "amount": {"value": "1299.00", "currency": "RUB"},
    }
    return payment, order, provider_payment


def test_yookassa_payment_binding_accepts_exact_match():
    from backend.api.payments import _validate_yookassa_payment_binding

    payment, order, provider_payment = _payment_binding_data()

    _validate_yookassa_payment_binding(payment, order, provider_payment)


def test_yookassa_payment_binding_accepts_legacy_response_without_metadata():
    from backend.api.payments import _validate_yookassa_payment_binding

    payment, order, provider_payment = _payment_binding_data()
    provider_payment.pop("metadata")

    _validate_yookassa_payment_binding(payment, order, provider_payment)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("payment_order_id", 8),
        ("provider_payment_id", "other-payment"),
        ("provider_order_id", "8"),
        ("amount", "1.00"),
        ("currency", "USD"),
    ],
)
def test_yookassa_payment_binding_rejects_mismatch(field, value):
    from backend.api.payments import _validate_yookassa_payment_binding

    payment, order, provider_payment = _payment_binding_data()
    if field == "payment_order_id":
        payment.order_id = value
    elif field == "provider_payment_id":
        provider_payment["id"] = value
    elif field == "provider_order_id":
        provider_payment["metadata"]["order_id"] = value
    elif field == "amount":
        provider_payment["amount"]["value"] = value
    else:
        provider_payment["amount"][field] = value

    with pytest.raises(HTTPException) as exc:
        _validate_yookassa_payment_binding(payment, order, provider_payment)

    assert exc.value.status_code == 409


def _create_yookassa_order(testing_session):
    from backend.models import Customer, Order, Payment

    with testing_session() as db:
        customer = Customer(telegram_id="payment-customer")
        db.add(customer)
        db.flush()
        order = Order(customer_id=customer.id, total_amount=1299.0, currency="RUB")
        db.add(order)
        db.flush()
        payment = Payment(
            order_id=order.id,
            provider="yookassa",
            provider_payment_id="payment-123",
            status="pending",
            amount=1299.0,
        )
        db.add(payment)
        db.commit()
        return order.id


def test_yookassa_webhook_endpoint_rejects_order_substitution_without_side_effects(webhook_api, monkeypatch):
    from backend.models import Customer, Order, PaymentEvent

    client, testing_session, payments = webhook_api
    original_order_id = _create_yookassa_order(testing_session)
    with testing_session() as db:
        other_customer = Customer(telegram_id="other-customer")
        db.add(other_customer)
        db.flush()
        other_order = Order(customer_id=other_customer.id, total_amount=1299.0, currency="RUB")
        db.add(other_order)
        db.commit()
        other_order_id = other_order.id

    async def should_not_fetch(_payment_id):
        raise AssertionError("provider lookup must not run for a substituted order")

    monkeypatch.setattr(payments, "fetch_yookassa_payment", should_not_fetch)
    response = client.post(
        "/api/payments/webhook/yookassa",
        json={
            "event": "payment.succeeded",
            "object": {"id": "payment-123", "metadata": {"order_id": str(other_order_id)}},
        },
    )

    assert response.status_code == 409
    with testing_session() as db:
        assert db.get(Order, original_order_id).payment_status == "pending"
        assert db.get(Order, other_order_id).payment_status == "pending"
        assert db.query(PaymentEvent).count() == 0


def test_yookassa_webhook_endpoint_accepts_legacy_provider_metadata(webhook_api, monkeypatch):
    from backend.models import Order, PaymentEvent

    client, testing_session, payments = webhook_api
    order_id = _create_yookassa_order(testing_session)

    async def legacy_provider_payment(_payment_id):
        return {
            "id": "payment-123",
            "status": "canceled",
            "amount": {"value": "1299.00", "currency": "RUB"},
        }

    monkeypatch.setattr(payments, "fetch_yookassa_payment", legacy_provider_payment)
    response = client.post(
        "/api/payments/webhook/yookassa",
        json={
            "event": "payment.canceled",
            "object": {"id": "payment-123"},
        },
    )

    assert response.status_code == 200
    with testing_session() as db:
        order = db.get(Order, order_id)
        assert order.status == "cancelled"
        assert order.payment_status == "cancelled"
        assert db.query(PaymentEvent).filter(PaymentEvent.processed.is_(True)).count() == 1
