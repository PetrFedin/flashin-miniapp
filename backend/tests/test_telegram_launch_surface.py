import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from check_telegram_bot import validate_launch_surface

BOT_MAIN = ROOT / "bot" / "main.py"


def test_exact_default_menu_web_app_matches_production_url():
    status = validate_launch_surface(
        {"id": 1, "has_main_web_app": True},
        {
            "type": "web_app",
            "text": "Open FLASHIN",
            "web_app": {"url": "https://MINI.flashin.store/"},
        },
        "https://mini.flashin.store",
    )

    assert status == {
        "identity_verified": True,
        "menu_button_verified": True,
        "launch_url_verified": True,
        "main_web_app_configured": True,
    }


def test_main_web_app_flag_is_advisory_when_default_menu_url_is_exact():
    status = validate_launch_surface(
        {"id": 1},
        {"type": "web_app", "web_app": {"url": "https://mini.flashin.store"}},
        "https://mini.flashin.store/",
    )

    assert status["launch_url_verified"] is True
    assert status["main_web_app_configured"] is False


def test_non_web_app_default_menu_is_rejected():
    with pytest.raises(ValueError, match="not a Mini App"):
        validate_launch_surface(
            {"id": 1},
            {"type": "commands"},
            "https://mini.flashin.store",
        )


def test_wrong_remote_launch_url_is_rejected_without_echoing_it():
    with pytest.raises(ValueError, match="does not match MINI_APP_URL") as exc:
        validate_launch_surface(
            {"id": 1},
            {"type": "web_app", "web_app": {"url": "https://wrong.invalid"}},
            "https://mini.flashin.store",
        )

    assert "wrong.invalid" not in str(exc.value)


def test_expected_launch_url_must_be_https():
    with pytest.raises(ValueError, match="must use HTTPS"):
        validate_launch_surface(
            {"id": 1},
            {"type": "web_app", "web_app": {"url": "https://mini.flashin.store"}},
            "http://mini.flashin.store",
        )


def test_bot_start_button_uses_same_mini_app_url_configuration():
    source = BOT_MAIN.read_text(encoding="utf-8")

    assert 'MINI_APP_URL = os.getenv("MINI_APP_URL"' in source
    assert "WebAppInfo(url=url)" in source
    assert "url = MINI_APP_URL if product_id is None" in source
