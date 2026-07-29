import pytest
from pydantic import ValidationError

from backend.config import Settings


def _settings(**overrides):
    values = {
        "telegram_bot_token": "test-token",
        "jwt_secret": "test-secret",
        "admin_login_max_failures": 5,
        "admin_login_failure_window_minutes": 15,
        "admin_login_lockout_minutes": 15,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def test_admin_login_lockout_defaults_are_safe():
    settings = _settings()

    assert settings.admin_login_max_failures == 5
    assert settings.admin_login_failure_window_minutes == 15
    assert settings.admin_login_lockout_minutes == 15


@pytest.mark.parametrize("value", [2, 21])
def test_admin_login_failure_threshold_is_bounded(value):
    with pytest.raises(ValidationError) as exc_info:
        _settings(admin_login_max_failures=value)

    assert "ADMIN_LOGIN_MAX_FAILURES" in str(exc_info.value)


@pytest.mark.parametrize("field", ["admin_login_failure_window_minutes", "admin_login_lockout_minutes"])
@pytest.mark.parametrize("value", [0, 1441])
def test_admin_login_time_windows_are_bounded(field, value):
    with pytest.raises(ValidationError) as exc_info:
        _settings(**{field: value})

    assert field.upper() in str(exc_info.value)
