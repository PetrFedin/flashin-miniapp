#!/usr/bin/env python3
"""Fail when the resolved production Compose graph is incomplete or unsafe."""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path

PUBLIC_SERVICE = "caddy"
REQUIRED_INTERNAL_SERVICES = {
    "db",
    "backend",
    "frontend",
    "admin",
    "bot",
    "notification_worker",
    "scheduler",
    "meilisearch",
}
EXPECTED_PUBLIC_PORTS = {(80, "tcp"), (443, "tcp")}
PRODUCTION_PROFILES = ("production", "workers", "scheduler", "search")


def _published_ports(service: Mapping) -> set[tuple[int, str]]:
    published: set[tuple[int, str]] = set()
    for entry in service.get("ports") or []:
        if not isinstance(entry, Mapping):
            raise ValueError("Compose ports must use normalized long syntax")
        raw_published = entry.get("published")
        if raw_published in (None, ""):
            continue
        try:
            port = int(raw_published)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Invalid published port: {raw_published}") from exc
        protocol = str(entry.get("protocol") or "tcp").lower()
        published.add((port, protocol))
    return published


def validate_config(config: Mapping) -> list[str]:
    services = config.get("services")
    if not isinstance(services, Mapping):
        return ["Compose configuration has no services map"]

    errors: list[str] = []
    missing = sorted((REQUIRED_INTERNAL_SERVICES | {PUBLIC_SERVICE}) - set(services))
    if missing:
        errors.append("Missing production services: " + ", ".join(missing))

    for name, service in services.items():
        if not isinstance(service, Mapping):
            errors.append(f"Service {name} has invalid configuration")
            continue
        try:
            ports = _published_ports(service)
        except ValueError as exc:
            errors.append(f"Service {name}: {exc}")
            continue

        if name == PUBLIC_SERVICE:
            if ports != EXPECTED_PUBLIC_PORTS:
                errors.append(
                    "Caddy must publish only 80/tcp and 443/tcp; got "
                    + repr(sorted(ports))
                )
        elif ports:
            errors.append(f"Internal service {name} publishes host ports: {sorted(ports)}")

        if service.get("network_mode") == "host":
            errors.append(f"Service {name} must not use host network mode")

    return errors


def compose_config_command(root: Path) -> list[str]:
    command = [
        "docker",
        "compose",
        "-f",
        str(root / "docker-compose.yml"),
        "-f",
        str(root / "docker-compose.production.yml"),
    ]
    for profile in PRODUCTION_PROFILES:
        command.extend(("--profile", profile))
    command.extend(("config", "--format", "json"))
    return command


def load_compose_config(root: Path | None = None) -> dict:
    project_root = (root or Path(__file__).resolve().parents[1]).resolve()
    result = subprocess.run(
        compose_config_command(project_root),
        cwd=project_root,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        error = result.stderr.strip() or result.stdout.strip() or "docker compose config failed"
        raise RuntimeError(error)
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("docker compose returned invalid JSON") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("docker compose configuration must be a JSON object")
    return payload


def main() -> int:
    try:
        config = load_compose_config()
        errors = validate_config(config)
    except Exception as exc:
        print({"ok": False, "error": f"{exc.__class__.__name__}: {exc}"})
        return 2

    if errors:
        print({"ok": False, "errors": errors})
        return 1
    print(
        {
            "ok": True,
            "public_service": PUBLIC_SERVICE,
            "ports": [80, 443],
            "profiles": list(PRODUCTION_PROFILES),
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
