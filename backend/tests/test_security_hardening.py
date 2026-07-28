import hashlib
import hmac
import time
from urllib.parse import urlencode

import pytest
from fastapi import HTTPException

from backend.config import get_settings
from backend.security import (
    _legacy_password_hash,
    hash_password,
    password_needs_rehash,
    verify_password,
    verify_telegram_init_data,
)


def _telegram_init_data(**overrides):
    fields = {
        "auth_date": str(int(time.time())),
        "query_id": "test-query",
        "user": '{"id":123,"first_name":"Test"}',
    }
    fields.update(overrides)
    data_check_string = "\n".join(f"{key}={value}" for key, value in sorted(fields.items()))
    secret_key = hmac.new(
        key=b"WebAppData",
        msg=get_settings().telegram_bot_token.encode("utf-8"),
        digestmod=hashlib.sha256,
    ).digest()
    fields["hash"] = hmac.new(
        key=secret_key,
        msg=data_check_string.encode("utf-8"),
        digestmod=hashlib.sha256,
    ).hexdigest()
    return urlencode(fields)


def test_password_hash_is_salted_and_verifiable():
    first = hash_password("strong-password")
    second = hash_password("strong-password")

    assert first != second
    assert verify_password("strong-password", first)
    assert not verify_password("wrong-password", first)
    assert not password_needs_rehash(first)


def test_legacy_password_hash_remains_compatible():
    legacy = _legacy_password_hash("legacy-password")

    assert verify_password("legacy-password", legacy)
    assert not verify_password("wrong-password", legacy)
    assert password_needs_rehash(legacy)


def test_valid_telegram_init_data_is_accepted():
    parsed = verify_telegram_init_data(_telegram_init_data())

    assert parsed["query_id"] == "test-query"
    assert parsed["auth_date"]


@pytest.mark.parametrize(
    "auth_date",
    [
        str(int(time.time()) - 60 * 60 * 25),
        str(int(time.time()) + 60 * 10),
        "0",
        "invalid",
    ],
)
def test_invalid_telegram_auth_date_is_rejected(auth_date):
    with pytest.raises(HTTPException) as exc:
        verify_telegram_init_data(_telegram_init_data(auth_date=auth_date))

    assert exc.value.status_code == 401


def test_duplicate_telegram_fields_are_rejected():
    valid = _telegram_init_data()

    with pytest.raises(HTTPException) as exc:
        verify_telegram_init_data(f"{valid}&auth_date={int(time.time())}")

    assert exc.value.status_code == 401
