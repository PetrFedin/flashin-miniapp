from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from backend.api.outbox import _reset_for_retry
from backend.database import utcnow_naive
from backend.services.outbox import enqueue_webhook, schedule_retry
from backend.services.webhook_security import (
    is_internal_destination,
    normalize_webhook_url,
)


class DummyDb:
    def __init__(self):
        self.added = []

    def add(self, value):
        self.added.append(value)


def test_public_https_destination_is_normalized():
    assert (
        normalize_webhook_url("HTTPS://Hooks.Example.com:443/orders?source=flashin", production=True)
        == "https://hooks.example.com/orders?source=flashin"
    )


def test_production_rejects_http_and_private_destinations():
    with pytest.raises(ValueError, match="must use https"):
        normalize_webhook_url("http://hooks.example.com/events", production=True)
    with pytest.raises(ValueError, match="private or reserved"):
        normalize_webhook_url("https://127.0.0.1/events", production=True)
    with pytest.raises(ValueError, match="hostname is not allowed"):
        normalize_webhook_url("https://localhost/events", production=True)


def test_credentials_fragments_and_internal_scheme_are_rejected():
    with pytest.raises(ValueError, match="must not contain credentials"):
        normalize_webhook_url("https://user:pass@hooks.example.com/events", production=True)
    with pytest.raises(ValueError, match="must not contain a fragment"):
        normalize_webhook_url("https://hooks.example.com/events#debug", production=True)
    with pytest.raises(ValueError, match="not external webhooks"):
        normalize_webhook_url("internal://order-paid", production=True)


def test_internal_destination_does_not_create_outbox_record():
    db = DummyDb()

    created = enqueue_webhook(db, "internal://order-paid", "order.paid", {"order_id": 1})

    assert created is False
    assert db.added == []
    assert is_internal_destination("internal://order-paid") is True


def test_retry_becomes_terminal_after_attempt_limit():
    row = SimpleNamespace(
        attempts=9,
        status="pending",
        last_error="",
        next_attempt_at=None,
    )

    schedule_retry(row, "provider failed")

    assert row.attempts == 10
    assert row.status == "failed"
    assert row.next_attempt_at is None
    assert row.last_error == "provider failed"


def test_failed_outbox_row_is_fully_reset_for_retry():
    row = SimpleNamespace(
        destination="https://hooks.example.com/events",
        status="failed",
        attempts=10,
        last_error="provider failed",
        next_attempt_at=None,
    )
    now = utcnow_naive()

    _reset_for_retry(row, now)

    assert row.status == "pending"
    assert row.attempts == 0
    assert row.last_error == ""
    assert row.next_attempt_at == now


def test_sent_outbox_row_cannot_be_retried():
    row = SimpleNamespace(
        destination="https://hooks.example.com/events",
        status="sent",
        attempts=1,
        last_error="",
        next_attempt_at=None,
    )

    with pytest.raises(HTTPException) as exc_info:
        _reset_for_retry(row, utcnow_naive())

    assert exc_info.value.status_code == 409
