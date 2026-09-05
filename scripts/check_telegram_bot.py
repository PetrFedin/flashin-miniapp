#!/usr/bin/env python3
"""Read-only Telegram Bot API pilot launch-surface probe with bounded output."""

from __future__ import annotations

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


def _bot_api_result(token: str, method: str) -> Mapping[str, object]:
    request = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/{method}",
        headers={
            "Accept": "application/json",
            "User-Agent": "flashin-telegram-probe/3.0",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"Telegram {method} failed: HTTP {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise RuntimeError(f"Telegram {method} failed: {exc.__class__.__name__}") from exc

    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Telegram {method} failed: invalid response") from exc
    result = payload.get("result") if isinstance(payload, dict) else None
    if (
        not isinstance(payload, dict)
        or payload.get("ok") is not True
        or not isinstance(result, Mapping)
    ):
        raise RuntimeError(f"Telegram {method} failed: invalid response")
    return result


def validate_launch_surface(
    identity: Mapping[str, object],
    menu_button: Mapping[str, object],
    expected_url: str,
) -> dict[str, bool]:
    if not identity.get("id"):
        raise ValueError("Telegram identity result is invalid")

    normalized_expected = _normalize_web_app_url(expected_url)
    if menu_button.get("type") != "web_app":
        raise ValueError("Telegram default menu button is not a Mini App")
    web_app = menu_button.get("web_app")
    if not isinstance(web_app, Mapping) or not isinstance(web_app.get("url"), str):
        raise ValueError("Telegram default menu button has no Mini App URL")
    if _normalize_web_app_url(str(web_app["url"])) != normalized_expected:
        raise ValueError("Telegram default menu Mini App URL does not match MINI_APP_URL")

    return {
        "identity_verified": True,
        "menu_button_verified": True,
        "launch_url_verified": True,
        "main_web_app_configured": identity.get("has_main_web_app") is True,
    }


def main() -> int:
    token = (os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("BOT_TOKEN") or "").strip()
    mini_app_url = os.getenv("MINI_APP_URL", "").strip()
    if not token:
        print("Telegram probe failed: bot token is required")
        return 1
    if not mini_app_url:
        print("Telegram probe failed: MINI_APP_URL is required")
        return 1

    try:
        identity = _bot_api_result(token, "getMe")
        menu_button = _bot_api_result(token, "getChatMenuButton")
        status = validate_launch_surface(identity, menu_button, mini_app_url)
    except (RuntimeError, ValueError) as exc:
        print(f"Telegram probe failed: {exc}")
        return 1

    print(
        json.dumps(
            {
                "status": "ok",
                "provider": "telegram",
                **status,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
