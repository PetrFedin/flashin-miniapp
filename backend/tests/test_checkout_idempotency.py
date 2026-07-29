import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.api.orders import _checkout_fingerprint, _clean_idempotency_key, router
from backend.checkout_idempotency_models import CheckoutIdempotency
from backend.database import Base, get_db
from backend.models import Cart, CartItem, Customer, Order, Product, ProductVariant
from backend.schemas import CheckoutIn
from backend.security import create_access_token


@pytest.fixture()
def checkout_api():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    testing_session = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    Base.metadata.create_all(bind=engine)

    with testing_session() as db:
        customer = Customer(
            telegram_id="checkout-idempotency-user",
            username="checkout-user",
            first_name="Petr",
        )
        product = Product(
            sku="CHECKOUT-IDEMPOTENCY-PRODUCT",
            title="Checkout Test Product",
            slug="checkout-idempotency-product",
            price=12990,
            currency="RUB",
            active=True,
        )
        db.add_all([customer, product])
        db.flush()

        variant = ProductVariant(
            product_id=product.id,
            size="M",
            color="black",
            sku="CHECKOUT-IDEMPOTENCY-PRODUCT-M",
            stock_qty=5,
            reserved_qty=0,
        )
        cart = Cart(customer_id=customer.id, status="active")
        db.add_all([variant, cart])
        db.flush()
        db.add(
            CartItem(
                cart_id=cart.id,
                product_id=product.id,
                variant_id=variant.id,
                quantity=1,
            )
        )
        db.commit()
        customer_id = customer.id
        variant_id = variant.id

    app = FastAPI()
    app.include_router(router, prefix="/api")

    def override_get_db():
        with testing_session() as db:
            yield db

    app.dependency_overrides[get_db] = override_get_db
    token = create_access_token(customer_id)

    with TestClient(app) as client:
        yield client, token, testing_session, customer_id, variant_id

    Base.metadata.drop_all(bind=engine)
    engine.dispose()


def _payload(**overrides):
    payload = {
        "name": "Petr Fedin",
        "phone": "+7 999 000-00-00",
        "delivery_type": "pickup",
        "address": "",
        "comment": "Test checkout",
    }
    payload.update(overrides)
    return payload


def _headers(token: str, key: str | None = None) -> dict[str, str]:
    result = {"Authorization": f"Bearer {token}"}
    if key is not None:
        result["Idempotency-Key"] = key
    return result


def test_checkout_requires_idempotency_key(checkout_api):
    client, token, testing_session, _, _ = checkout_api

    response = client.post(
        "/api/orders/checkout",
        headers=_headers(token),
        json=_payload(),
    )

    assert response.status_code == 422
    with testing_session() as db:
        assert db.query(Order).count() == 0
        assert db.query(CheckoutIdempotency).count() == 0


def test_checkout_rejects_invalid_idempotency_key(checkout_api):
    client, token, testing_session, _, _ = checkout_api

    response = client.post(
        "/api/orders/checkout",
        headers=_headers(token, "short key"),
        json=_payload(),
    )

    assert response.status_code == 400
    assert "Idempotency-Key" in response.json()["detail"]
    with testing_session() as db:
        assert db.query(Order).count() == 0
        assert db.query(CheckoutIdempotency).count() == 0


def test_repeated_checkout_returns_same_order_without_double_reservation(checkout_api):
    client, token, testing_session, customer_id, variant_id = checkout_api
    key = "checkout-retry-1234567890"

    first = client.post(
        "/api/orders/checkout",
        headers=_headers(token, key),
        json=_payload(),
    )
    second = client.post(
        "/api/orders/checkout",
        headers=_headers(token, key),
        json=_payload(),
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["id"] == second.json()["id"]
    assert first.headers["idempotency-replayed"] == "false"
    assert second.headers["idempotency-replayed"] == "true"

    with testing_session() as db:
        assert db.query(Order).filter(Order.customer_id == customer_id).count() == 1
        assert db.query(CheckoutIdempotency).count() == 1
        record = db.query(CheckoutIdempotency).one()
        assert record.order_id == first.json()["id"]
        variant = db.query(ProductVariant).filter(ProductVariant.id == variant_id).one()
        assert variant.reserved_qty == 1
        cart = db.query(Cart).filter(Cart.customer_id == customer_id).one()
        assert cart.status == "converted"


def test_same_key_with_different_payload_is_rejected(checkout_api):
    client, token, testing_session, _, _ = checkout_api
    key = "checkout-conflict-1234567890"

    first = client.post(
        "/api/orders/checkout",
        headers=_headers(token, key),
        json=_payload(),
    )
    conflict = client.post(
        "/api/orders/checkout",
        headers=_headers(token, key),
        json=_payload(comment="Changed checkout data"),
    )

    assert first.status_code == 200
    assert conflict.status_code == 409
    assert "different checkout data" in conflict.json()["detail"]
    with testing_session() as db:
        assert db.query(Order).count() == 1
        assert db.query(CheckoutIdempotency).count() == 1


def test_failed_checkout_does_not_consume_idempotency_key(checkout_api):
    client, token, testing_session, _, _ = checkout_api
    key = "checkout-recover-1234567890"

    failed = client.post(
        "/api/orders/checkout",
        headers=_headers(token, key),
        json=_payload(delivery_type="courier", address=""),
    )
    recovered = client.post(
        "/api/orders/checkout",
        headers=_headers(token, key),
        json=_payload(delivery_type="courier", address="Moscow, Tverskaya 1"),
    )

    assert failed.status_code == 400
    assert recovered.status_code == 200
    assert recovered.headers["idempotency-replayed"] == "false"
    with testing_session() as db:
        assert db.query(Order).count() == 1
        assert db.query(CheckoutIdempotency).count() == 1


def test_checkout_fingerprint_normalizes_equivalent_input():
    first = CheckoutIn(**_payload(name="  Petr Fedin  ", delivery_type=" PICKUP "))
    second = CheckoutIn(**_payload(name="Petr Fedin", delivery_type="pickup"))

    assert _checkout_fingerprint(first) == _checkout_fingerprint(second)


def test_clean_idempotency_key_accepts_uuid_shape():
    key = "550e8400-e29b-41d4-a716-446655440000"

    assert _clean_idempotency_key(key) == key
