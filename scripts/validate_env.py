#!/usr/bin/env python3
from pathlib import Path
import sys

env_path = Path(".env")
if not env_path.exists():
    print(".env not found")
    sys.exit(1)

env = {}
for line in env_path.read_text().splitlines():
    if "=" in line and not line.strip().startswith("#"):
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip()

required = [
    "DATABASE_URL",
    "TELEGRAM_BOT_TOKEN",
    "JWT_SECRET",
    "ADMIN_EMAIL",
    "ADMIN_PASSWORD",
    "MINI_APP_URL",
    "API_PUBLIC_URL",
]

weak = []
missing = []
for key in required:
    value = env.get(key)
    if not value:
        missing.append(key)
    if value in {"change-me", "change-this-before-launch", "replace_with_botfather_token", "replace_with_long_random_secret"}:
        weak.append(key)

if missing or weak:
    print({"missing": missing, "weak_defaults": weak})
    sys.exit(1)

print("Environment OK")
