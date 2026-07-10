#!/usr/bin/env python3
"""FLASHIN e2e smoke checks.

This script checks live endpoints. It does not fake payment success.
Run after `make init`:

    API_BASE=http://localhost:8000 python tests/e2e_smoke.py
"""
import os
import sys
import urllib.request
import urllib.parse
import json

API = os.getenv("API_BASE", "http://localhost:8000").rstrip("/")

def get(path):
    with urllib.request.urlopen(API + path, timeout=10) as resp:
        return resp.status, resp.read().decode()

checks = [
    ("/health", 200),
    ("/ready", 200),
    ("/", 200),
    ("/api/products", 200),
    ("/api/looks", 200),
]

failed = []
for path, expected in checks:
    try:
        status, body = get(path)
        print(path, status)
        if status != expected:
            failed.append((path, status))
    except Exception as exc:
        print(path, "FAILED", exc)
        failed.append((path, str(exc)))

if failed:
    print("Failed:", failed)
    sys.exit(1)

print("E2E smoke OK")
