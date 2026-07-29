import pytest
from fastapi import HTTPException

from backend.api.admin_auth import _validate_new_admin_password
from backend.middleware.rate_limit import _ADMIN_AUTH_ROUTES


def test_strong_admin_password_is_accepted():
    _validate_new_admin_password("Fresh-Admin-Password-2026!", "owner@flashin.store")


def test_common_admin_password_is_rejected():
    with pytest.raises(HTTPException) as exc_info:
        _validate_new_admin_password("password123", "owner@flashin.store")

    assert exc_info.value.status_code == 400


def test_password_requires_three_character_classes():
    with pytest.raises(HTTPException) as exc_info:
        _validate_new_admin_password("onlylowercasepassword", "owner@flashin.store")

    assert exc_info.value.status_code == 400


def test_password_must_not_contain_email_local_part():
    with pytest.raises(HTTPException) as exc_info:
        _validate_new_admin_password("Owner-Strong-Password-2026!", "owner@flashin.store")

    assert exc_info.value.status_code == 400


def test_password_reset_confirmation_uses_admin_auth_rate_limit():
    assert "/api/admin/password-reset/confirm" in _ADMIN_AUTH_ROUTES
