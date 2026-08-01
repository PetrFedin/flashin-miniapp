from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from backend.api.orders import checkout
from backend.schemas import CheckoutIn


class DatabaseMustNotBeTouched:
    def query(self, *_args, **_kwargs):
        raise AssertionError("invalid checkout reached the database")

    def add(self, *_args, **_kwargs):
        raise AssertionError("invalid checkout created a database object")

    def flush(self):
        raise AssertionError("invalid checkout flushed a transaction")

    def commit(self):
        raise AssertionError("invalid checkout committed a transaction")

    def rollback(self):
        raise AssertionError("validation must happen before the transaction")


def invoke(payload):
    return checkout(
        payload,
        idempotency_key_header="checkout-validation-0001",
        customer=SimpleNamespace(id=17),
        db=DatabaseMustNotBeTouched(),
    )


@pytest.mark.parametrize(
    ("payload", "detail"),
    [
        (
            CheckoutIn(
                name="Petr",
                phone="call-me-1234567",
                delivery_type="pickup",
                address="",
                comment="",
            ),
            "Phone number is invalid",
        ),
        (
            CheckoutIn(
                name="Petr",
                phone="+46 70 123 45 67",
                delivery_type="drone",
                address="",
                comment="",
            ),
            "Unsupported delivery type",
        ),
        (
            CheckoutIn(
                name="Petr",
                phone="+46 70 123 45 67",
                delivery_type="courier",
                address="short",
                comment="",
            ),
            "Courier address is too short",
        ),
        (
            CheckoutIn(
                name="Petr",
                phone="+46 70 123 45 67",
                delivery_type="pickup",
                address="",
                comment="x" * 1001,
            ),
            "Comment is too long",
        ),
    ],
)
def test_invalid_checkout_is_rejected_before_database_access(payload, detail):
    with pytest.raises(HTTPException) as error:
        invoke(payload)

    assert error.value.status_code == 400
    assert error.value.detail == detail
