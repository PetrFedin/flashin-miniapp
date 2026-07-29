#!/usr/bin/env python3
import secrets
from pathlib import Path


TEMPLATE = Path(".env.production.example") if Path(".env.production.example").exists() else Path(".env.local.example")
TARGET = Path(".env")

if not TEMPLATE.exists():
    raise SystemExit("Environment template not found")

defaults = {}
for line in TEMPLATE.read_text().splitlines():
    if "=" in line and not line.strip().startswith("#"):
        key, value = line.split("=", 1)
        defaults[key] = value

questions = {
    "TELEGRAM_BOT_TOKEN": "Telegram bot token from BotFather",
    "JWT_SECRET": "JWT secret",
    "ADMIN_EMAIL": "Admin email",
    "ADMIN_PASSWORD": "Admin password",
    "ADMIN_TOTP_ENCRYPTION_KEY": "Separate TOTP encryption key",
    "MINI_APP_URL": "Mini App URL",
    "API_PUBLIC_URL": "API public URL",
    "ADMIN_URL": "Admin URL",
    "YOOKASSA_SHOP_ID": "YooKassa shop ID",
    "YOOKASSA_SECRET_KEY": "YooKassa secret key",
    "MOYSKLAD_TOKEN": "MoySklad token",
    "MEILISEARCH_MASTER_KEY": "Meilisearch master key",
    "OUTBOX_SIGNING_SECRET": "Outbox signing secret",
}

for generated_key in (
    "JWT_SECRET",
    "ADMIN_TOTP_ENCRYPTION_KEY",
    "OUTBOX_SIGNING_SECRET",
):
    if generated_key in defaults and not defaults[generated_key]:
        defaults[generated_key] = secrets.token_urlsafe(48)

if (
    defaults.get("ADMIN_TOTP_ENCRYPTION_KEY")
    and defaults.get("ADMIN_TOTP_ENCRYPTION_KEY") == defaults.get("JWT_SECRET")
):
    defaults["ADMIN_TOTP_ENCRYPTION_KEY"] = secrets.token_urlsafe(48)

print("FLASHIN setup wizard")
print("Press Enter to keep current/default value.")
print("Secrets are written to .env locally. Do not commit .env.")

values = dict(defaults)
for key, prompt in questions.items():
    current = values.get(key, "")
    masked = "***" if current and any(marker in key for marker in ["SECRET", "TOKEN", "PASSWORD", "KEY"]) else current
    user_value = input(f"{prompt} [{masked}]: ").strip()
    if user_value:
        values[key] = user_value

if values.get("ADMIN_TOTP_ENCRYPTION_KEY") == values.get("JWT_SECRET"):
    raise SystemExit("ADMIN_TOTP_ENCRYPTION_KEY must differ from JWT_SECRET")

lines = []
for line in TEMPLATE.read_text().splitlines():
    if "=" in line and not line.strip().startswith("#"):
        key, _ = line.split("=", 1)
        lines.append(f"{key}={values.get(key, '')}")
    else:
        lines.append(line)

TARGET.write_text("\n".join(lines) + "\n", encoding="utf-8")
print(".env written")
print("Next: python3 scripts/validate_env.py && make init")
