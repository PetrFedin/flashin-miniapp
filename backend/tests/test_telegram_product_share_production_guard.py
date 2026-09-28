from pathlib import Path

from backend.services.telegram_product_links import telegram_bot_username

ROOT = Path(__file__).resolve().parents[2]
MAIN = ROOT / "backend" / "main.py"
PRODUCTION_ENV = ROOT / ".env.production.example"


def test_template_username_is_not_accepted_as_runtime_configuration():
    assert telegram_bot_username({"TELEGRAM_BOT_USERNAME": "replace_with_bot_username"}) == ""
    assert telegram_bot_username({"TELEGRAM_BOT_USERNAME": "your_bot"}) == ""
    assert telegram_bot_username({"TELEGRAM_BOT_USERNAME": "@FlashinPilotBot"}) == "FlashinPilotBot"


def test_production_template_declares_public_bot_username_setting():
    source = PRODUCTION_ENV.read_text(encoding="utf-8")
    assert "TELEGRAM_BOT_USERNAME=replace_with_bot_username" in source


def test_production_backend_fails_closed_when_product_deep_link_username_is_missing():
    source = MAIN.read_text(encoding="utf-8")
    assert "if is_production and not telegram_bot_username():" in source
    assert "TELEGRAM_BOT_USERNAME must be configured in production" in source
