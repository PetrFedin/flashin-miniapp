from urllib.parse import parse_qs, urlparse

from backend.services.telegram_product_links import (
    product_share_links,
    telegram_bot_username,
)


def test_product_share_uses_startapp_when_bot_username_is_configured():
    links = product_share_links(
        41,
        "Cashmere Jacket",
        mini_app_url="https://mini.flashin.store",
        env={"TELEGRAM_BOT_USERNAME": "FlashinPilotBot"},
    )

    assert links["web_url"] == "https://mini.flashin.store?product=41"
    assert links["mini_app_deep_link"] == "https://t.me/FlashinPilotBot?startapp=product_41"
    share = urlparse(links["telegram_share_url"])
    params = parse_qs(share.query)
    assert params["url"] == [links["mini_app_deep_link"]]
    assert params["text"] == ["Cashmere Jacket"]


def test_product_share_falls_back_to_web_without_valid_username():
    links = product_share_links(
        7,
        "Pilot Product",
        mini_app_url="https://mini.flashin.store/",
        env={"TELEGRAM_BOT_USERNAME": "bad-name!"},
    )

    assert telegram_bot_username({"TELEGRAM_BOT_USERNAME": "@FlashinPilotBot"}) == "FlashinPilotBot"
    assert telegram_bot_username({"TELEGRAM_BOT_USERNAME": "bad-name!"}) == ""
    assert links["mini_app_deep_link"] == ""
    assert parse_qs(urlparse(links["telegram_share_url"]).query)["url"] == [links["web_url"]]
