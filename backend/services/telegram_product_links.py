from __future__ import annotations

import os
import re
from urllib.parse import quote

_USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{5,32}$")


def telegram_bot_username(env: dict[str, str] | None = None) -> str:
    source = env if env is not None else os.environ
    username = str(source.get("TELEGRAM_BOT_USERNAME") or "").strip().lstrip("@")
    return username if _USERNAME_RE.fullmatch(username) else ""


def product_share_links(
    product_id: int,
    title: str,
    *,
    mini_app_url: str,
    env: dict[str, str] | None = None,
) -> dict[str, str]:
    product_id = int(product_id)
    if product_id <= 0:
        raise ValueError("product_id must be positive")
    web_url = f"{str(mini_app_url).rstrip('/')}?product={product_id}"
    username = telegram_bot_username(env)
    deep_link = (
        f"https://t.me/{username}?startapp=product_{product_id}"
        if username
        else ""
    )
    share_target = deep_link or web_url
    telegram_share_url = (
        "https://t.me/share/url"
        f"?url={quote(share_target, safe='')}"
        f"&text={quote(str(title or '').strip(), safe='')}"
    )
    return {
        "web_url": web_url,
        "mini_app_deep_link": deep_link,
        "telegram_share_url": telegram_share_url,
    }
