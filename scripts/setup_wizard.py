#!/usr/bin/env python3
from pathlib import Path
import secrets

TEMPLATE = Path(".env.production.example") if Path(".env.production.example").exists() else Path(".env.local.example")
TARGET = Path(".env")

defaults = {}
if TEMPLATE.exists():
    for line in TEMPLATE.read_text().splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k, v = line.split("=", 1)
            defaults[k] = v

questions = {
    "TELEGRAM_BOT_TOKEN": "Telegram bot token from BotFather",
    "JWT_SECRET": "JWT secret",
    "ADMIN_EMAIL": "Admin email",
    "ADMIN_PASSWORD": "Admin password",
    "MINI_APP_URL": "Mini App URL",
    "API_PUBLIC_URL": "API public URL",
    "ADMIN_URL": "Admin URL",
    "YOOKASSA_SHOP_ID": "YooKassa shop ID",
    "YOOKASSA_SECRET_KEY": "YooKassa secret key",
    "MOYSKLAD_TOKEN": "MoySklad token",
    "MEILISEARCH_MASTER_KEY": "Meilisearch master key",
    "OUTBOX_SIGNING_SECRET": "Outbox signing secret",
}

if "JWT_SECRET" in defaults and not defaults["JWT_SECRET"]:
    defaults["JWT_SECRET"] = secrets.token_urlsafe(48)
if "OUTBOX_SIGNING_SECRET" in defaults and not defaults["OUTBOX_SIGNING_SECRET"]:
    defaults["OUTBOX_SIGNING_SECRET"] = secrets.token_urlsafe(48)

print("FLASHIN setup wizard")
print("Press Enter to keep current/default value.")
print("Secrets are written to .env locally. Do not commit .env.")

values = dict(defaults)
for key, prompt in questions.items():
    current = values.get(key, "")
    masked = "***" if current and any(x in key for x in ["SECRET", "TOKEN", "PASSWORD", "KEY"]) else current
    user_value = input(f"{prompt} [{masked}]: ").strip()
    if user_value:
        values[key] = user_value

lines = []
for line in TEMPLATE.read_text().splitlines():
    if "=" in line and not line.strip().startswith("#"):
        k, _ = line.split("=", 1)
        lines.append(f"{k}={values.get(k, '')}")
    else:
        lines.append(line)

TARGET.write_text("\n".join(lines) + "\n", encoding="utf-8")
print(".env written")
print("Next: python3 scripts/validate_env.py && make init")
