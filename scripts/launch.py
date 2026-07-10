#!/usr/bin/env python3
import argparse
import secrets
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV = ROOT / ".env"

REQUIRED = [
    "DATABASE_URL",
    "TELEGRAM_BOT_TOKEN",
    "JWT_SECRET",
    "ADMIN_EMAIL",
    "ADMIN_PASSWORD",
    "MINI_APP_URL",
    "API_PUBLIC_URL",
    "ADMIN_URL",
]

AUTO_SECRET_KEYS = {
    "JWT_SECRET",
    "OUTBOX_SIGNING_SECRET",
    "MEILISEARCH_MASTER_KEY",
}

def run(cmd, required=True):
    print(f"\n$ {cmd}")
    result = subprocess.run(cmd, shell=True, cwd=ROOT)
    if required and result.returncode != 0:
        sys.exit(result.returncode)
    return result.returncode

def read_env_template(mode):
    template = ROOT / (".env.production.example" if mode == "production" else ".env.local.example")
    return template.read_text(encoding="utf-8")

def write_env_if_missing(mode):
    if ENV.exists():
        print(".env already exists")
        return
    content = read_env_template(mode)
    lines = []
    for line in content.splitlines():
        if "=" in line and not line.strip().startswith("#"):
            key, val = line.split("=", 1)
            if key in AUTO_SECRET_KEYS and not val:
                val = secrets.token_urlsafe(48)
            if key == "ADMIN_PASSWORD" and not val:
                val = secrets.token_urlsafe(16)
            lines.append(f"{key}={val}")
        else:
            lines.append(line)
    ENV.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Created .env from {mode} template")

def validate_minimum_env():
    if not ENV.exists():
        print(".env is missing")
        return False
    env = ENV.read_text(encoding="utf-8")
    missing = []
    for key in REQUIRED:
        if f"{key}=" not in env:
            missing.append(key)
    if missing:
        print("Missing required keys:", missing)
        return False
    return True

def main():
    parser = argparse.ArgumentParser(description="FLASHIN one-command launcher")
    parser.add_argument("--mode", choices=["local", "production"], default="local")
    parser.add_argument("--with-search", action="store_true")
    parser.add_argument("--with-monitoring", action="store_true")
    parser.add_argument("--with-workers", action="store_true")
    parser.add_argument("--skip-build", action="store_true")
    args = parser.parse_args()

    write_env_if_missing(args.mode)

    if not validate_minimum_env():
        sys.exit(1)

    run("python3 scripts/preflight.py")
    run("python3 scripts/validate_env.py", required=False)

    if not args.skip_build:
        run("docker compose build")

    run("docker compose up -d db")
    run("docker compose run --rm backend alembic -c backend/alembic.ini upgrade head")
    run("docker compose run --rm backend python scripts/seed_admin.py", required=False)

    if args.with_search:
        run("docker compose --profile search up -d meilisearch")
        run("docker compose run --rm backend python scripts/configure_meilisearch.py", required=False)

    run("docker compose up -d backend frontend admin bot")

    if args.with_workers:
        run("docker compose --profile workers up -d", required=False)

    if args.with_monitoring:
        run("docker compose --profile monitoring up -d", required=False)

    run("python3 tests/e2e_smoke.py", required=False)
    run("python3 scripts/production_readiness_report.py", required=False)

    print("\nFLASHIN is started")
    print("Mini App: http://localhost:5173")
    print("Admin:    http://localhost:5174")
    print("API:      http://localhost:8000/docs")
    print("\nNext: fill real Telegram/YooKassa/MoySklad values and run 20-order pilot.")

if __name__ == "__main__":
    main()
