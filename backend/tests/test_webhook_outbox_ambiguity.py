import socket
from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException

from backend.api import outbox as outbox_api
from backend.services.webhook_delivery import (
    AMBIGUOUS_HTTP,
    AMBIGUOUS_TRANSPORT,
    PERMANENT_CONTRACT,
    PERMANENT_HTTP,
    SAFE_RETRY_PRE_DISPATCH,
    classify_http_status,
    classify_pre_dispatch_exception,
    classify_transport_exception,
    delivery_classification_from_error,
    encode_delivery_error,
)


def _request():
    return httpx.Request("POST", "https://hooks.example.test/event")


def test_connect_failures_are_the_only_transport_failures_safe_for_automatic_retry():
    assert (
        classify_transport_exception(httpx.ConnectError("connect", request=_request()))
        == SAFE_RETRY_PRE_DISPATCH
    )
    assert (
        classify_transport_exception(httpx.ConnectTimeout("connect", request=_request()))
        == SAFE_RETRY_PRE_DISPATCH
    )
    assert (
        classify_transport_exception(httpx.PoolTimeout("pool", request=_request()))
        == SAFE_RETRY_PRE_DISPATCH
    )
    assert (
        classify_transport_exception(httpx.ReadTimeout("lost response", request=_request()))
        == AMBIGUOUS_TRANSPORT
    )
    assert (
        classify_transport_exception(httpx.WriteTimeout("write", request=_request()))
        == AMBIGUOUS_TRANSPORT
    )


def test_predispatch_dns_failure_retries_but_contract_failure_requires_review():
    dns = ValueError("Webhook hostname could not be resolved")
    dns.__cause__ = socket.gaierror("dns")
    assert classify_pre_dispatch_exception(dns) == SAFE_RETRY_PRE_DISPATCH
    assert classify_pre_dispatch_exception(ValueError("invalid payload")) == PERMANENT_CONTRACT
    assert classify_pre_dispatch_exception(RuntimeError("unsafe signing secret")) == PERMANENT_CONTRACT


def test_non_2xx_http_outcomes_never_blindly_replay():
    assert classify_http_status(400) == PERMANENT_HTTP
    assert classify_http_status(404) == PERMANENT_HTTP
    assert classify_http_status(408) == AMBIGUOUS_HTTP
    assert classify_http_status(429) == AMBIGUOUS_HTTP
    assert classify_http_status(500) == AMBIGUOUS_HTTP
    assert classify_http_status(302) == AMBIGUOUS_HTTP


def test_delivery_error_storage_is_bounded_and_public_projection_hides_raw_error():
    encoded = encode_delivery_error(AMBIGUOUS_TRANSPORT, "ReadTimeout")
    assert encoded == "classification=ambiguous_transport;error=ReadTimeout"
    assert delivery_classification_from_error(encoded) == AMBIGUOUS_TRANSPORT

    row = SimpleNamespace(
        id=19,
        destination="https://hooks.example.test/path?token=secret",
        event_type="order.created",
        status="review_required",
        attempts=1,
        last_error=encoded,
    )
    public = outbox_api._public_outbox(row)
    assert public["classification"] == AMBIGUOUS_TRANSPORT
    assert public["last_error"] == "Webhook delivery outcome requires review"
    assert "secret" not in public["destination"]


def test_generic_retry_cannot_bypass_review_required():
    row = SimpleNamespace(
        status="review_required",
        destination="https://hooks.example.test/event",
        attempts=1,
        last_error=encode_delivery_error(AMBIGUOUS_TRANSPORT, "ReadTimeout"),
        next_attempt_at=None,
    )
    with pytest.raises(HTTPException) as exc_info:
        outbox_api._reset_for_retry(row, None)
    assert exc_info.value.status_code == 409


def test_review_action_requires_exact_event_identity_and_bounded_reason_code():
    payload = SimpleNamespace(
        event_id=77,
        reason_code="receiver_confirmed_processed",
    )
    assert outbox_api._validate_review_action(77, payload) == "receiver_confirmed_processed"

    with pytest.raises(HTTPException) as exc_info:
        outbox_api._validate_review_action(78, payload)
    assert exc_info.value.status_code == 400

    invalid = SimpleNamespace(event_id=77, reason_code="free form maybe secret")
    with pytest.raises(HTTPException) as exc_info:
        outbox_api._validate_review_action(77, invalid)
    assert exc_info.value.status_code == 400
