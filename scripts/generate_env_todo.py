#!/usr/bin/env python3
import json
from pathlib import Path


env_file = Path(".env")
template = Path(".env.production.example") if Path(".env.production.example").exists() else Path(".env.local.example")

required = {
    "TELEGRAM_BOT_TOKEN": "BotFather token",
    "JWT_SECRET": "long random customer/admin JWT secret",
    "ADMIN_EMAIL": "initial admin login",
    "ADMIN_PASSWORD": "initial admin password",
    "ADMIN_TOTP_ENCRYPTION_KEY": "separate random key for encrypting administrator TOTP secrets",
    "YOOKASSA_SHOP_ID": "YooKassa shop id",
    "YOOKASSA_SECRET_KEY": "YooKassa secret key",
    "MOYSKLAD_TOKEN": "MoySklad API token",
    "MEILISEARCH_MASTER_KEY": "Meilisearch master key",
    "OUTBOX_SIGNING_SECRET": "webhook signing secret",
}

weak = {
    "",
    "change-me",
    "change-this-before-launch",
    "replace_with_botfather_token",
    "replace_with_long_random_secret",
}

values = {}
if env_file.exists():
    for line in env_file.read_text().splitlines():
        if "=" in line and not line.strip().startswith("#"):
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip()

todo = []
for key, why in required.items():
    value = values.get(key, "")
    if value in weak or not value:
        todo.append({"key": key, "why": why, "status": "missing_or_default"})
    else:
        todo.append({"key": key, "why": why, "status": "filled", "masked": "***"})

Path("docs/env_todo.json").write_text(
    json.dumps(todo, ensure_ascii=False, indent=2),
    encoding="utf-8",
)
Path("docs/env_todo.md").write_text(
    "# ENV TODO\n\n"
    + "\n".join(
        [
            f"- [{'x' if item['status'] == 'filled' else ' '}] {item['key']} — {item['why']}"
            for item in todo
        ]
    )
    + "\n",
    encoding="utf-8",
)
print(
    {
        "written": "docs/env_todo.md",
        "missing": [item["key"] for item in todo if item["status"] != "filled"],
    }
)
