from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from backend import security
from backend.api.privacy import _request_type
from backend.services.privacy import (
    ALLOWED_CONSENT_TYPES,
    OPTIONAL_CONSENT_TYPES,
    mark_privacy_processed,
)
from backend.services.rbac import DEFAULT_PERMISSIONS


class FakeQuery:
    def __init__(self, value):
        self.value = value

    def filter(self, *args, **kwargs):
        return self

    def first(self):
        return self.value


class FakeDb:
    def __init__(self, customer):
        self.customer = customer

    def query(self, model):
        return FakeQuery(self.customer)


def test_privacy_request_type_is_normalized_and_validated():
    assert _request_type(" DELETE ") == "delete"

    with pytest.raises(HTTPException) as exc_info:
        _request_type("unknown")

    assert exc_info.value.status_code == 400


def test_optional_consents_are_explicit_subset():
    assert OPTIONAL_CONSENT_TYPES == {"marketing", "analytics", "personalization"}
    assert OPTIONAL_CONSENT_TYPES < ALLOWED_CONSENT_TYPES


def test_manager_can_review_but_not_execute_privacy_deletion():
    assert "privacy.read" in DEFAULT_PERMISSIONS["manager"]
    assert "privacy.write" not in DEFAULT_PERMISSIONS["manager"]


def test_anonymized_customer_token_is_rejected():
    customer = SimpleNamespace(id=11, telegram_id="deleted:11:abcdef")
    db = FakeDb(customer)
    token = security.create_access_token(customer.id)
    credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)

    with pytest.raises(HTTPException) as exc_info:
        security.get_current_customer(credentials=credentials, db=db)

    assert exc_info.value.status_code == 401


def test_processed_privacy_request_records_completion():
    request = SimpleNamespace(status="processing", result_url="", processed_at=None)

    mark_privacy_processed(request, "/api/privacy/export")

    assert request.status == "processed"
    assert request.result_url == "/api/privacy/export"
    assert request.processed_at is not None
