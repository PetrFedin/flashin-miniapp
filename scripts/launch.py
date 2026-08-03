#!/usr/bin/env python3
"""FLASHIN launcher.

Local mode is a convenience bootstrap. Production mode deliberately delegates to
scripts/deploy_production.sh so the hardened Compose overlay, backup, migrations,
readiness checks and service validation cannot be bypassed.
"""

from __future__ import annotations

import argparse
import os
import secrets
import subprocess
from pathlib import Path
from typing import Sequence

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
    "ADMIN_TOTP_ENCRYPTION_KEY",
    "OUTBOX_SIGNING_SECRET",
    "MEILISEARCH_MASTER_KEY",
}


def run(command: Sequence[str], *, required: bool = True, env: dict[str, str] | None = None) -> int:
    printable = " ".join(command)
    print(f"\n$ {printable}")
    result = subprocess.run(list(command), cwd=ROOT, env=env, check=False)
    if required and result.returncode != 0:
        raise SystemExit(result.returncode)
    return result.returncode


def read_env_template(mode: str) -> str:
    template = ROOT / (".env.production.example" if mode == "production" else ".env.local.example")
    return template.read_text(encoding="utf-8")


def write_env_if_missing(mode: str) -> None:
    if ENV.exists():
        print(".env already exists")
        return
    content = read_env_template(mode)
    lines: list[str] = []
    for line in content.splitlines():
        if "=" in line and not line.strip().startswith("#"):
            key, value = line.split("=", 1)
            if key in AUTO_SECRET_KEYS and not value:
                value = secrets.token_urlsafe(48)
            if key == "ADMIN_PASSWORD" and not value:
                value = secrets.token_urlsafe(20)
            lines.append(f"{key}={value}")
        else:
            lines.append(line)
    ENV.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Created .env from {mode} template")


def load_env_values() -> dict[str, str]:
    values: dict[str, str] = {}
    if not ENV.exists():
        return values
    for raw_line in ENV.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def validate_minimum_env() -> bool:
    values = load_env_values()
    missing = [key for key in REQUIRED if not values.get(key)]
    if missing:
        print("Missing or empty required keys:", missing)
        return False
    return True


def launch_production(args: argparse.Namespace) -> None:
    if args.skip_build:
        raise SystemExit("--skip-build is not allowed in production mode")
    if args.with_workers is False or args.with_search is False:
        print("Production deploy always starts required workers and search services.")
    run(["bash", "scripts/deploy_production.sh"])
    if args.with_monitoring:
        compose_env = os.environ.copy()
        compose_env["COMPOSE_FILE"] = "docker-compose.yml:docker-compose.production.yml"
        compose_env["COMPOSE_PROFILES"] = "production,workers,scheduler,search,monitoring"
        run(
            ["docker", "compose", "--profile", "monitoring", "up", "-d", "prometheus", "grafana"],
            env=compose_env,
        )
    print("\nProduction services are deployed. Run `make pilot-gate` before admitting pilot users.")


def launch_local(args: argparse.Namespace) -> None:
    run(["python3", "scripts/preflight.py"])
    run(["python3", "scripts/validate_env.py"], required=False)

    if not args.skip_build:
        run(["docker", "compose", "build"])

    run(["docker", "compose", "up", "-d", "db"])
    run(
        [
            "docker",
            "compose",
            "run",
            "--rm",
            "backend",
            "alembic",
            "-c",
            "backend/alembic.ini",
            "upgrade",
            "head",
        ]
    )
    run(
        ["docker", "compose", "run", "--rm", "backend", "python", "scripts/seed_admin.py"],
        required=False,
    )

    if args.with_search:
        run(["docker", "compose", "--profile", "search", "up", "-d", "meilisearch"])
        run(
            ["docker", "compose", "run", "--rm", "backend", "python", "scripts/configure_meilisearch.py"],
            required=False,
        )

    run(["docker", "compose", "up", "-d", "backend", "frontend", "admin", "bot"])

    if args.with_workers:
        run(["docker", "compose", "--profile", "workers", "up", "-d"], required=False)
    if args.with_monitoring:
        run(
            ["docker", "compose", "--profile", "monitoring", "up", "-d", "prometheus", "grafana"],
            required=False,
        )

    run(["python3", "tests/e2e_smoke.py"], required=False)
    run(["python3", "scripts/production_readiness_report.py"], required=False)

    print("\nFLASHIN local environment is started")
    print("Mini App: http://localhost:5173")
    print("Admin:    http://localhost:5174")
    print("API:      http://localhost:8000/docs")


def main() -> None:
    parser = argparse.ArgumentParser(description="FLASHIN one-command launcher")
    parser.add_argument("--mode", choices=["local", "production"], default="local")
    parser.add_argument("--with-search", action="store_true")
    parser.add_argument("--with-monitoring", action="store_true")
    parser.add_argument("--with-workers", action="store_true")
    parser.add_argument("--skip-build", action="store_true")
    args = parser.parse_args()

    write_env_if_missing(args.mode)
    if not validate_minimum_env():
        raise SystemExit(1)

    if args.mode == "production":
        launch_production(args)
    else:
        launch_local(args)


if __name__ == "__main__":
    main()
