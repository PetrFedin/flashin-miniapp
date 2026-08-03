#!/usr/bin/env python3
"""Read-only MoySklad credential and product API probe."""

import base64
import json
import os
import urllib.error
import urllib.parse
import urllib.request


def _authorization() -> str:
    token = (os.getenv("MOYSKLAD_TOKEN") or "").strip()
    if token:
        return f"Bearer {token}"
    login = (os.getenv("MOYSKLAD_LOGIN") or "").strip()
    password = os.getenv("MOYSKLAD_PASSWORD") or ""
    if login and password:
        encoded = base64.b64encode(f"{login}:{password}".encode("utf-8")).decode("ascii")
        return f"Basic {encoded}"
    return ""


def main() -> int:
    base_url = (os.getenv("MOYSKLAD_BASE_URL") or "https://api.moysklad.ru/api/remap/1.2").rstrip("/")
    authorization = _authorization()
    if not authorization:
        print("Configure MOYSKLAD_TOKEN or MOYSKLAD_LOGIN and MOYSKLAD_PASSWORD")
        return 1

    query = urllib.parse.urlencode({"limit": 1})
    request = urllib.request.Request(
        f"{base_url}/entity/product?{query}",
        headers={
            "Accept": "application/json;charset=utf-8",
            "Authorization": authorization,
            "User-Agent": "flashin-moysklad-probe/1.0",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=25) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        print(f"MoySklad probe failed: HTTP {exc.code}")
        return 1
    except Exception as exc:
        print(f"MoySklad probe failed: {exc.__class__.__name__}: {exc}")
        return 1

    rows = payload.get("rows") if isinstance(payload, dict) else None
    meta = payload.get("meta") if isinstance(payload, dict) else None
    if not isinstance(rows, list) or not isinstance(meta, dict):
        print("MoySklad probe failed: invalid product collection response")
        return 1
    total_products = meta.get("size")
    if not isinstance(total_products, int) or total_products <= 0:
        print("MoySklad probe failed: product catalog is empty")
        return 1
    print(
        json.dumps(
            {
                "status": "ok",
                "sampled_rows": len(rows),
                "total_products": total_products,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
