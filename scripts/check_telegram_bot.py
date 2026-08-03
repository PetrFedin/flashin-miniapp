#!/usr/bin/env python3
"""Read-only Telegram Bot API credential probe."""

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
        headers={"Accept": "application/json", "User-Agent": "flashin-telegram-probe/1.0"},
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        print(f"Telegram probe failed: HTTP {exc.code}")
        return 1
    except Exception as exc:
        print(f"Telegram probe failed: {exc.__class__.__name__}: {exc}")
        return 1

    result = payload.get("result") if isinstance(payload, dict) else None
    if not isinstance(payload, dict) or not payload.get("ok") or not isinstance(result, dict) or not result.get("id"):
        print("Telegram probe failed: invalid getMe response")
        return 1
    print(
        json.dumps(
            {
                "status": "ok",
                "bot_id": result.get("id"),
                "username": result.get("username"),
                "can_join_groups": result.get("can_join_groups"),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
