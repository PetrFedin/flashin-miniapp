#!/usr/bin/env python3
"""Idempotently configure and verify FLASHIN's default Telegram Mini App menu button."""

from __future__ import annotations

import argparse
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping


def _normalize_web_app_url(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        raise ValueError("Mini App URL is missing")
    try:
        parsed = urllib.parse.urlsplit(raw)
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Mini App URL is invalid") from exc
    if parsed.scheme.lower() != "https" or not parsed.hostname:
        raise ValueError("Mini App URL must use HTTPS")
    if parsed.username or parsed.password or parsed.fragment:
        raise ValueError("Mini App URL contains unsupported URL components")
    if port not in (None, 443):
        raise ValueError("Mini App URL must use the standard HTTPS port")
    host = parsed.hostname.rstrip(".").lower()
    path = parsed.path.rstrip("/") or "/"
    normalized = f"https://{host}{path}"
    if parsed.query:
        normalized += f"?{parsed.query}"
    return normalized


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


HTTP_OPENER = urllib.request.build_opener(_NoRedirect)


def _bot_api(token: str, method: str, payload: dict | None = None):
    headers = {
        "Accept": "application/json",
        "User-Agent": "flashin-telegram-launch-config/1.0",
    }
    data = None
    http_method = "GET"
    if payload is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        http_method = "POST"
    request = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/{method}",
        data=data,
        headers=headers,
        method=http_method,
    )
    try:
        with HTTP_OPENER.open(request, timeout=20) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"Telegram {method} failed: HTTP {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise RuntimeError(f"Telegram {method} failed: {exc.__class__.__name__}") from exc
    try:
        decoded = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Telegram {method} failed: invalid response") from exc
    if not isinstance(decoded, dict) or decoded.get("ok") is not True:
        raise RuntimeError(f"Telegram {method} failed: invalid response")
    return decoded.get("result")


def menu_matches(menu_button: object, expected_url: str, expected_text: str) -> bool:
    if not isinstance(menu_button, Mapping) or menu_button.get("type") != "web_app":
        return False
    web_app = menu_button.get("web_app")
    if not isinstance(web_app, Mapping) or not isinstance(web_app.get("url"), str):
        return False
    if str(menu_button.get("text") or "") != expected_text:
        return False
    try:
        remote_url = _normalize_web_app_url(str(web_app["url"]))
    except ValueError:
        return False
    return remote_url == expected_url


def parser() -> argparse.ArgumentParser:
    command = argparse.ArgumentParser(description=__doc__)
    command.add_argument(
        "--acknowledge-provider-change",
        action="store_true",
        help="Allow changing Telegram's default menu button when read-back differs.",
    )
    return command


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    token = (os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("BOT_TOKEN") or "").strip()
    mini_app_url = os.getenv("MINI_APP_URL", "").strip()
    text = os.getenv("TELEGRAM_MENU_BUTTON_TEXT", "Open FLASHIN").strip()
    if not token:
        print("Telegram launch configuration failed: bot token is required")
        return 1
    if not text or len(text) > 64:
        print("Telegram launch configuration failed: menu button text must contain 1-64 characters")
        return 1

    try:
        expected_url = _normalize_web_app_url(mini_app_url)
        current = _bot_api(token, "getChatMenuButton")
        if menu_matches(current, expected_url, text):
            print(json.dumps({
                "status": "ok", "provider": "telegram",
                "changed": False, "read_back_verified": True,
            }))
            return 0

        if not args.acknowledge_provider_change:
            print("Telegram launch configuration failed: provider change is required; rerun with --acknowledge-provider-change")
            return 1

        result = _bot_api(token, "setChatMenuButton", {
            "menu_button": {
                "type": "web_app",
                "text": text,
                "web_app": {"url": expected_url},
            }
        })
        if result is not True:
            raise RuntimeError("Telegram setChatMenuButton failed: invalid response")
        read_back = _bot_api(token, "getChatMenuButton")
        if not menu_matches(read_back, expected_url, text):
            raise RuntimeError("Telegram menu button read-back does not match requested configuration")
        print(json.dumps({
            "status": "ok", "provider": "telegram",
            "changed": True, "read_back_verified": True,
        }))
        return 0
    except (RuntimeError, ValueError) as exc:
        print(f"Telegram launch configuration failed: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
