#!/usr/bin/env python3
"""Fail-closed static wiring preflight for FLASHIN pilot providers.

This check is intentionally side-effect free. It validates that operator-facing
configuration connects the production URLs and worker switches into one coherent
Telegram -> YooKassa -> FLASHIN -> MoySklad/notification path. Live credentials
and provider reachability remain the responsibility of check_integrations.py.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Mapping
from urllib.parse import urlparse

try:
    from .pilot_readiness import read_env
except ImportError:  # Direct execution: python scripts/provider_wiring_preflight.py
    from pilot_readiness import read_env

ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class WiringCheck:
    name: str
    ok: bool
    detail: str


def _truthy(value: object) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _present(value: object) -> bool:
    normalized = str(value or "").strip()
    if not normalized:
        return False
    return normalized.lower() not in {
        "change-me",
        "change-me-now",
        "replace-me",
        "replace_with_botfather_token",
        "replace_with_long_random_secret",
        "strong_password",
    }


def _https_url(value: object) -> tuple[bool, str]:
    raw = str(value or "").strip()
    try:
        parsed = urlparse(raw)
        port = parsed.port
    except ValueError:
        return False, "invalid URL or port"
    if parsed.scheme != "https" or not parsed.hostname:
        return False, "must be an absolute HTTPS URL"
    if parsed.username or parsed.password:
        return False, "credentials must not be embedded in public URLs"
    if port not in {None, 443, 8443}:
        return False, "HTTPS port must be 443 or 8443"
    return True, "HTTPS URL"


def validate_wiring(env: Mapping[str, str]) -> dict[str, object]:
    checks: list[WiringCheck] = []

    def add(name: str, ok: bool, detail: str) -> None:
        checks.append(WiringCheck(name, bool(ok), detail))

    add(
        "app_env_production",
        str(env.get("APP_ENV", "")).strip().lower() == "production",
        "APP_ENV must be production for pilot provider wiring",
    )

    for key in ("MINI_APP_URL", "API_PUBLIC_URL", "ADMIN_URL", "YOOKASSA_RETURN_URL", "YOOKASSA_WEBHOOK_URL"):
        ok, detail = _https_url(env.get(key))
        add(f"https:{key.lower()}", ok, detail)

    api_public = str(env.get("API_PUBLIC_URL", "")).strip().rstrip("/")
    webhook_url = str(env.get("YOOKASSA_WEBHOOK_URL", "")).strip().rstrip("/")
    expected_webhook = f"{api_public}/api/webhooks/yookassa" if api_public else ""
    add(
        "yookassa_canonical_webhook",
        bool(expected_webhook) and webhook_url == expected_webhook,
        "YOOKASSA_WEBHOOK_URL must equal API_PUBLIC_URL + /api/webhooks/yookassa",
    )

    mini_app = str(env.get("MINI_APP_URL", "")).strip().rstrip("/")
    return_url = str(env.get("YOOKASSA_RETURN_URL", "")).strip().rstrip("/")
    add(
        "yookassa_return_to_mini_app",
        bool(mini_app) and return_url == f"{mini_app}/payment-result",
        "YOOKASSA_RETURN_URL must point to MINI_APP_URL/payment-result",
    )

    telegram_primary = str(env.get("TELEGRAM_BOT_TOKEN", "")).strip()
    telegram_compat = str(env.get("BOT_TOKEN", "")).strip()
    add(
        "telegram_credentials",
        _present(telegram_primary or telegram_compat),
        "Telegram bot token is configured",
    )
    add(
        "telegram_token_alias_consistency",
        not telegram_primary or not telegram_compat or telegram_primary == telegram_compat,
        "TELEGRAM_BOT_TOKEN and BOT_TOKEN must match when both are set",
    )

    add("yookassa_shop_id", _present(env.get("YOOKASSA_SHOP_ID")), "YooKassa shop id is configured")
    add("yookassa_secret", _present(env.get("YOOKASSA_SECRET_KEY")), "YooKassa secret is configured")

    moy_token = _present(env.get("MOYSKLAD_TOKEN"))
    moy_basic = _present(env.get("MOYSKLAD_LOGIN")) and _present(env.get("MOYSKLAD_PASSWORD"))
    add("moysklad_credentials", moy_token or moy_basic, "MoySklad token or login/password is configured")

    export_enabled = _truthy(env.get("MOYSKLAD_ORDER_EXPORT_ENABLED"))
    add("moysklad_order_export", export_enabled, "MoySklad outbound order documents must be enabled")
    if export_enabled:
        for key in ("MOYSKLAD_ORGANIZATION_ID", "MOYSKLAD_AGENT_ID", "MOYSKLAD_STORE_ID"):
            add(f"moysklad:{key.lower()}", _present(env.get(key)), f"{key} is configured")

    add("scheduler_enabled", _truthy(env.get("SCHEDULER_ENABLED")), "Scheduler must process provider commands and reconciliation")
    add("pilot_runtime_enforced", _truthy(env.get("PILOT_RUNTIME_ENFORCED")), "Controlled pilot checkout runtime must be enforced")

    failed = [check for check in checks if not check.ok]
    return {
        "go": not failed,
        "summary": {"total": len(checks), "passed": len(checks) - len(failed), "failed": len(failed)},
        "checks": [asdict(check) for check in checks],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env", default=str(ROOT / ".env"), help="Path to production environment file")
    args = parser.parse_args(argv)
    report = validate_wiring(read_env(Path(args.env)))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["go"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
