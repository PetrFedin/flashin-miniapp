#!/usr/bin/env python3
"""Run smoke checks from inside the backend container and Docker network."""

import os
import sys
import urllib.error
import urllib.request

API_BASE = os.getenv("SMOKE_API_BASE", "http://localhost:8000").rstrip("/")
SEARCH_BASE = os.getenv("SMOKE_SEARCH_BASE", "http://meilisearch:7700").rstrip("/")

CHECKS = [
    ("backend", API_BASE, "/health", 200),
    ("backend", API_BASE, "/ready", 200),
    ("backend", API_BASE, "/", 200),
    ("backend", API_BASE, "/api/products", 200),
    ("backend", API_BASE, "/api/looks", 200),
    ("search", SEARCH_BASE, "/health", 200),
]


def check(name: str, base: str, path: str, expected: int) -> tuple[bool, str]:
    request = urllib.request.Request(
        base + path,
        headers={"User-Agent": "flashin-container-smoke/1.0"},
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            status = response.status
    except urllib.error.HTTPError as exc:
        status = exc.code
    except Exception as exc:
        return False, f"{name} {path}: {exc.__class__.__name__}"

    if status != expected:
        return False, f"{name} {path}: expected {expected}, got {status}"
    return True, f"{name} {path}: {status}"


def main() -> int:
    failures: list[str] = []
    for name, base, path, expected in CHECKS:
        ok, message = check(name, base, path, expected)
        print(message)
        if not ok:
            failures.append(message)

    if failures:
        print({"ok": False, "failures": failures})
        return 1
    print({"ok": True, "checks": len(CHECKS)})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
