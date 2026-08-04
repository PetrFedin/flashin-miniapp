#!/usr/bin/env python3
"""Fail when the resolved production Compose graph is incomplete or unsafe."""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

PUBLIC_SERVICE = "caddy"
WORKER_SERVICES = {
    "notification_worker",
    "ops_jobs",
    "outbox_jobs",
    "moysklad_sync",
    "campaign_jobs",
    "sla_jobs",
    "event_jobs",
    "media_jobs",
    "scheduler",
}
MONITORING_SERVICES = {"prometheus", "grafana"}
REQUIRED_INTERNAL_SERVICES = {
    "db",
    "backend",
    "frontend",
    "admin",
    "bot",
    "meilisearch",
} | WORKER_SERVICES | MONITORING_SERVICES
EXPECTED_PUBLIC_PORTS = {(80, "tcp"), (443, "tcp")}
PRODUCTION_PROFILES = ("production", "workers", "scheduler", "search", "monitoring")
RESTART_POLICY = "unless-stopped"


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


def _healthcheck_text(service: Mapping) -> str:
    healthcheck = service.get("healthcheck")
    if not isinstance(healthcheck, Mapping):
        return ""
    test = healthcheck.get("test")
    if isinstance(test, str):
        return test
    if isinstance(test, Sequence) and not isinstance(test, (str, bytes)):
        return " ".join(str(value) for value in test)
    return ""


def _dependency_condition(service: Mapping, dependency: str) -> str | None:
    depends_on = service.get("depends_on")
    if not isinstance(depends_on, Mapping):
        return None
    value = depends_on.get(dependency)
    if isinstance(value, Mapping):
        return str(value.get("condition") or "service_started")
    if value is not None:
        return "service_started"
    return None


def _mounts_by_target(service: Mapping) -> dict[str, Mapping]:
    result: dict[str, Mapping] = {}
    for mount in service.get("volumes") or []:
        if isinstance(mount, Mapping) and mount.get("target"):
            result[str(mount["target"])] = mount
    return result


def _pinned_image(service: Mapping) -> bool:
    image = str(service.get("image") or "").strip()
    if not image or ":" not in image:
        return False
    return not image.endswith(":latest")


def validate_config(config: Mapping) -> list[str]:
    services = config.get("services")
    if not isinstance(services, Mapping):
        return ["Compose configuration has no services map"]

    errors: list[str] = []
    required_services = REQUIRED_INTERNAL_SERVICES | {PUBLIC_SERVICE}
    missing = sorted(required_services - set(services))
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

        if name in required_services and service.get("restart") != RESTART_POLICY:
            errors.append(f"Service {name} must use restart: {RESTART_POLICY}")

    backend = services.get("backend")
    if isinstance(backend, Mapping):
        if "/ready" not in _healthcheck_text(backend):
            errors.append("Backend healthcheck must use /ready, not only liveness")
        if _dependency_condition(backend, "db") != "service_healthy":
            errors.append("Backend must wait for a healthy database")
        backend_mounts = _mounts_by_target(backend)
        for target in ("/app/docs", "/app/deploy/release"):
            mount = backend_mounts.get(target)
            if not mount:
                errors.append(f"Backend must mount {target}")
            elif not mount.get("read_only"):
                errors.append(f"Backend mount {target} must be read-only")

    for worker_name in sorted(WORKER_SERVICES):
        worker = services.get(worker_name)
        if isinstance(worker, Mapping) and _dependency_condition(worker, "db") != "service_healthy":
            errors.append(f"{worker_name} must wait for a healthy database")

    prometheus = services.get("prometheus")
    if isinstance(prometheus, Mapping):
        if not _pinned_image(prometheus):
            errors.append("Prometheus must use a pinned image tag")
        if "/-/ready" not in _healthcheck_text(prometheus):
            errors.append("Prometheus healthcheck must use /-/ready")
        if _dependency_condition(prometheus, "backend") != "service_healthy":
            errors.append("Prometheus must wait for a healthy backend")
        mounts = _mounts_by_target(prometheus)
        for target in ("/etc/prometheus/prometheus.yml", "/etc/prometheus/rules"):
            mount = mounts.get(target)
            if not mount:
                errors.append(f"Prometheus must mount {target}")
            elif not mount.get("read_only"):
                errors.append(f"Prometheus mount {target} must be read-only")

    grafana = services.get("grafana")
    if isinstance(grafana, Mapping):
        if not _pinned_image(grafana):
            errors.append("Grafana must use a pinned image tag")
        if "/api/health" not in _healthcheck_text(grafana):
            errors.append("Grafana healthcheck must use /api/health")
        if _dependency_condition(grafana, "prometheus") != "service_healthy":
            errors.append("Grafana must wait for healthy Prometheus")
        environment = grafana.get("environment")
        if not isinstance(environment, Mapping):
            errors.append("Grafana production environment is missing")
        else:
            if str(environment.get("GF_AUTH_ANONYMOUS_ENABLED", "")).lower() != "false":
                errors.append("Grafana anonymous access must be disabled")
            if str(environment.get("GF_USERS_ALLOW_SIGN_UP", "")).lower() != "false":
                errors.append("Grafana user sign-up must be disabled")
            for key in ("GF_SECURITY_ADMIN_USER", "GF_SECURITY_ADMIN_PASSWORD"):
                if not str(environment.get(key, "")).strip():
                    errors.append(f"Grafana production environment is missing {key}")

    caddy = services.get(PUBLIC_SERVICE)
    if isinstance(caddy, Mapping):
        for dependency in ("frontend", "admin", "backend"):
            if _dependency_condition(caddy, dependency) is None:
                errors.append(f"Caddy must depend on {dependency}")

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
            "backend_healthcheck": "/ready",
            "monitoring": sorted(MONITORING_SERVICES),
            "workers": sorted(WORKER_SERVICES),
            "restart_policy": RESTART_POLICY,
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
