#!/usr/bin/env python3
"""Render a production Alertmanager config without exposing its webhook secret."""

from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SECRET_ENV = ROOT / "deploy" / "secrets" / "alertmanager.env"
DEFAULT_OUTPUT = ROOT / "deploy" / "runtime" / "alertmanager.yml"
RUNTIME_DIRECTORY_MODE = 0o700
RUNTIME_CONFIG_MODE = 0o644
_PLACEHOLDER_MARKERS = (
    "replace",
    "change-me",
    "example.invalid",
    "example.com",
    "todo",
)


@dataclass(frozen=True)
class AlertmanagerSettings:
    webhook_url: str
    oncall_owner: str
    send_resolved: bool


def _read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _parse_bool(value: str, key: str) -> bool:
    normalized = str(value or "").strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{key} must be true or false")


def _validate_secret_file(path: Path) -> None:
    if path.is_symlink():
        raise ValueError("Alertmanager secret env must not be a symlink")
    if not path.is_file():
        raise ValueError(f"Alertmanager secret env is missing: {path}")
    mode = stat.S_IMODE(path.stat().st_mode)
    if os.name == "posix" and mode & 0o077:
        raise ValueError("Alertmanager secret env permissions must be 0600 or stricter")


def _validate_webhook_url(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        raise ValueError("ALERTMANAGER_WEBHOOK_URL is missing")
    lowered = raw.lower()
    if any(marker in lowered for marker in _PLACEHOLDER_MARKERS):
        raise ValueError("ALERTMANAGER_WEBHOOK_URL still contains a placeholder")

    try:
        parsed = urlparse(raw)
        port = parsed.port
    except ValueError as exc:
        raise ValueError(f"ALERTMANAGER_WEBHOOK_URL is invalid: {exc}") from exc

    if parsed.scheme.lower() != "https":
        raise ValueError("ALERTMANAGER_WEBHOOK_URL must use HTTPS")
    if not parsed.hostname:
        raise ValueError("ALERTMANAGER_WEBHOOK_URL hostname is missing")
    if parsed.username or parsed.password:
        raise ValueError("ALERTMANAGER_WEBHOOK_URL must not contain embedded credentials")
    if parsed.fragment:
        raise ValueError("ALERTMANAGER_WEBHOOK_URL must not contain a fragment")
    if port not in (None, 443):
        raise ValueError("ALERTMANAGER_WEBHOOK_URL must use the standard HTTPS port")

    host = parsed.hostname.rstrip(".").lower()
    blocked_suffixes = (
        ".localhost",
        ".local",
        ".internal",
        ".test",
        ".invalid",
        ".example",
    )
    if host == "localhost" or host.endswith(blocked_suffixes):
        raise ValueError("ALERTMANAGER_WEBHOOK_URL must use a public hostname")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        if "." not in host:
            raise ValueError("ALERTMANAGER_WEBHOOK_URL hostname must be fully qualified")
    else:
        if not address.is_global:
            raise ValueError("ALERTMANAGER_WEBHOOK_URL must use a public IP address")
    return raw


def load_settings(path: Path = DEFAULT_SECRET_ENV) -> AlertmanagerSettings:
    resolved = path.expanduser().resolve()
    _validate_secret_file(resolved)
    values = _read_env(resolved)

    owner = str(values.get("ALERTMANAGER_ONCALL_OWNER", "")).strip()
    if not 3 <= len(owner) <= 120:
        raise ValueError("ALERTMANAGER_ONCALL_OWNER must contain 3 to 120 characters")
    if "\n" in owner or "\r" in owner:
        raise ValueError("ALERTMANAGER_ONCALL_OWNER must be a single line")
    if any(marker in owner.lower() for marker in _PLACEHOLDER_MARKERS):
        raise ValueError("ALERTMANAGER_ONCALL_OWNER still contains a placeholder")

    return AlertmanagerSettings(
        webhook_url=_validate_webhook_url(values.get("ALERTMANAGER_WEBHOOK_URL", "")),
        oncall_owner=owner,
        send_resolved=_parse_bool(
            values.get("ALERTMANAGER_SEND_RESOLVED", "true"),
            "ALERTMANAGER_SEND_RESOLVED",
        ),
    )


def render_config(settings: AlertmanagerSettings) -> str:
    webhook = json.dumps(settings.webhook_url, ensure_ascii=False)
    send_resolved = "true" if settings.send_resolved else "false"
    return (
        "global:\n"
        "  resolve_timeout: 5m\n"
        "\n"
        "route:\n"
        "  receiver: flashin-pilot-oncall\n"
        "  group_by:\n"
        "    - alertname\n"
        "    - severity\n"
        "    - component\n"
        "  group_wait: 10s\n"
        "  group_interval: 2m\n"
        "  repeat_interval: 30m\n"
        "  routes:\n"
        "    - receiver: flashin-pilot-oncall\n"
        "      matchers:\n"
        "        - 'alertname=\"FlashinPilotAlertDeliverySmoke\"'\n"
        "      group_wait: 1s\n"
        "      group_interval: 1m\n"
        "      repeat_interval: 24h\n"
        "\n"
        "receivers:\n"
        "  - name: flashin-pilot-oncall\n"
        "    webhook_configs:\n"
        f"      - url: {webhook}\n"
        f"        send_resolved: {send_resolved}\n"
    )


def _write_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if os.name == "posix":
        # The deployment-only input remains 0600. The generated config is mounted
        # into a non-root Alertmanager container, so protect it on the host by a
        # private runtime directory while keeping the file itself container-readable.
        os.chmod(path.parent, RUNTIME_DIRECTORY_MODE)
    file_descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        dir=path.parent,
        text=True,
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(file_descriptor, RUNTIME_CONFIG_MODE)
        with os.fdopen(file_descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        os.chmod(path, RUNTIME_CONFIG_MODE)
    finally:
        temporary.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Render FLASHIN Alertmanager config")
    parser.add_argument("--secret-env", type=Path, default=DEFAULT_SECRET_ENV)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--check",
        action="store_true",
        help="validate inputs and render in memory without writing the runtime config",
    )
    args = parser.parse_args()

    settings = load_settings(args.secret_env)
    content = render_config(settings)
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    if not args.check:
        _write_atomic(args.output.expanduser().resolve(), content)

    print(
        json.dumps(
            {
                "ok": True,
                "mode": "check" if args.check else "rendered",
                "webhook_host": urlparse(settings.webhook_url).hostname,
                "oncall_owner_configured": True,
                "send_resolved": settings.send_resolved,
                "config_sha256": digest,
                "output": None if args.check else str(args.output),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
