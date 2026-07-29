from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from jose import jwt

from backend.config import get_settings
from backend.security import (
    _JWT_ADMIN_AUDIENCE,
    _JWT_CUSTOMER_AUDIENCE,
    _JWT_ISSUER,
    _decode_token,
    create_access_token,
    create_admin_token,
)


def test_customer_token_has_required_trust_claims():
    settings = get_settings()
    token = create_access_token(123)

    payload = jwt.decode(
        token,
        settings.jwt_secret,
        algorithms=[settings.jwt_algorithm],
        audience=_JWT_CUSTOMER_AUDIENCE,
        issuer=_JWT_ISSUER,
    )

    assert payload["sub"] == "123"
    assert payload["type"] == "customer"
    assert payload["aud"] == _JWT_CUSTOMER_AUDIENCE
    assert payload["jti"]


def test_admin_token_cannot_be_used_as_customer_token():
    token = create_admin_token(7, "owner")

    with pytest.raises(HTTPException) as exc_info:
        _decode_token(token, "customer", _JWT_CUSTOMER_AUDIENCE)

    assert exc_info.value.status_code == 401


def test_customer_token_cannot_be_used_as_admin_token():
    token = create_access_token(123)

    with pytest.raises(HTTPException) as exc_info:
        _decode_token(token, "admin", _JWT_ADMIN_AUDIENCE)

    assert exc_info.value.status_code == 401


def test_token_without_jti_is_rejected():
    settings = get_settings()
    now = datetime.now(timezone.utc)
    token = jwt.encode(
        {
            "sub": "123",
            "type": "customer",
            "iss": _JWT_ISSUER,
            "aud": _JWT_CUSTOMER_AUDIENCE,
            "iat": now,
            "nbf": now,
            "exp": now + timedelta(minutes=5),
        },
        settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
    )

    with pytest.raises(HTTPException) as exc_info:
        _decode_token(token, "customer", _JWT_CUSTOMER_AUDIENCE)

    assert exc_info.value.status_code == 401
