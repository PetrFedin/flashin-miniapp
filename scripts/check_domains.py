#!/usr/bin/env python3
import os
import sys
import urllib.request

URLS = [
    os.getenv("MINI_APP_URL", "https://mini.flashin.store"),
    os.getenv("API_PUBLIC_URL", "https://api.flashin.store") + "/health",
    os.getenv("ADMIN_URL", "https://admin.flashin.store"),
]

failed = []
for url in URLS:
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            print(url, resp.status)
            if resp.status >= 400:
                failed.append(url)
    except Exception as exc:
        print(url, "FAILED", exc)
        failed.append(url)

if failed:
    print("Domain check failed:", failed)
    sys.exit(1)

print("Domain check OK")
