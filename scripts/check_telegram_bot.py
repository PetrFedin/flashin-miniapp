#!/usr/bin/env python3
"""Read-only Telegram Bot API credential probe with bounded evidence output."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request


def main() -> int:
    token = (os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("BOT_TOKEN") or "").strip()
    if not token:
        print("TELEGRAM_BOT_TOKEN or BOT_TOKEN is required")
        return 1

    request = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/getMe",
        headers={"Accept": "application/json", "User-Agent": "flashin-telegram-probe/2.0"},
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        print(f"Telegram probe failed: HTTP {exc.code}")
        return 1
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        print(f"Telegram probe failed: {exc.__class__.__name__}")
        return 1
    except Exception as exc:
        print(f"Telegram probe failed: {exc.__class__.__name__}")
        return 1

    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        print("Telegram probe failed: invalid getMe response")
        return 1

    result = payload.get("result") if isinstance(payload, dict) else None
    if not isinstance(payload, dict) or not payload.get("ok") or not isinstance(result, dict) or not result.get("id"):
        print("Telegram probe failed: invalid getMe response")
        return 1

    print(
        json.dumps(
            {
                "status": "ok",
                "provider": "telegram",
                "identity_verified": True,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
