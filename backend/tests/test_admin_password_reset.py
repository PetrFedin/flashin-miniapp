import pytest

from backend.middleware.rate_limit import _ADMIN_AUTH_ROUTES
from backend.services.admin_password_policy import validate_admin_password


def test_strong_admin_password_is_accepted():
    validate_admin_password("Fresh-Admin-Password-2026!", "owner@flashin.store")


def test_common_admin_password_is_rejected():
    with pytest.raises(ValueError, match="too weak"):
        validate_admin_password("change-me-now", "owner@flashin.store")


def test_password_requires_three_character_classes():
    with pytest.raises(ValueError, match="three character classes"):
        validate_admin_password("onlylowercasepassword", "owner@flashin.store")


def test_password_must_not_contain_email_local_part():
    with pytest.raises(ValueError, match="email name"):
        validate_admin_password("Owner-Strong-Password-2026!", "owner@flashin.store")


def test_password_reset_confirmation_uses_admin_auth_rate_limit():
    assert "/api/admin/password-reset/confirm" in _ADMIN_AUTH_ROUTES
