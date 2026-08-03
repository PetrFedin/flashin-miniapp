#!/usr/bin/env python3
"""Read-only Meilisearch health and product-index probe."""

import json
import os
import urllib.error
import urllib.parse
import urllib.request


def _get_json(url: str, key: str) -> object:
    headers = {"Accept": "application/json", "User-Agent": "flashin-meilisearch-probe/1.0"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=15) as response:
        return json.loads(response.read().decode("utf-8"))


def main() -> int:
    base_url = (os.getenv("MEILISEARCH_URL") or "").rstrip("/")
    master_key = (os.getenv("MEILISEARCH_MASTER_KEY") or "").strip()
    index = (os.getenv("MEILISEARCH_PRODUCTS_INDEX") or "products").strip()
    if not base_url or not master_key:
        print("MEILISEARCH_URL and MEILISEARCH_MASTER_KEY are required")
        return 1

    try:
        health = _get_json(f"{base_url}/health", master_key)
        stats = _get_json(
            f"{base_url}/indexes/{urllib.parse.quote(index, safe='')}/stats",
            master_key,
        )
    except urllib.error.HTTPError as exc:
        print(f"Meilisearch probe failed: HTTP {exc.code}")
        return 1
    except Exception as exc:
        print(f"Meilisearch probe failed: {exc.__class__.__name__}: {exc}")
        return 1

    if not isinstance(health, dict) or health.get("status") != "available":
        print("Meilisearch probe failed: service is not available")
        return 1
    if not isinstance(stats, dict) or not isinstance(stats.get("numberOfDocuments"), int):
        print("Meilisearch probe failed: product index stats are invalid")
        return 1
    if stats["numberOfDocuments"] <= 0:
        print("Meilisearch probe failed: product index is empty")
        return 1

    print(
        json.dumps(
            {
                "status": "ok",
                "index": index,
                "documents": stats["numberOfDocuments"],
                "is_indexing": stats.get("isIndexing"),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
