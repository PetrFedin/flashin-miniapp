#!/usr/bin/env python3
"""Strict pre-deploy and live pilot GO/NO-GO gate."""

from __future__ import annotations

import argparse
import json
from datetime import timedelta
from pathlib import Path
from typing import Any, Mapping

from pilot_evidence import (
    configuration_fingerprint,
    load_json,
    release_binding,
    require_signing_secret,
    sign_payload,
    utc_now,
    utc_timestamp,
)
from pilot_readiness import (
    CheckResult,
    build_live_checks,
    build_predeploy_checks,
    build_report,
    read_env,
    write_report,
)
from provider_wiring_preflight import validate_wiring

ROOT = Path(__file__).resolve().parents[1]
CURRENT_RELEASE_PATH = ROOT / "deploy/release/runtime/current_release.json"


def _positive_int(env: Mapping[str, str], key: str, default: int) -> int:
    raw = str(env.get(key, default)).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{key} must be an integer") from exc
    if value < 5 or value > 120:
        raise ValueError(f"{key} must be between 5 and 120")
    return value


def _provider_wiring_checks(env: Mapping[str, str]) -> list[CheckResult]:
    report = validate_wiring(env)
    return [
        CheckResult(
            name=f"provider_wiring:{item['name']}",
            ok=item["ok"] is True,
            critical=True,
            detail=str(item.get("detail") or ""),
        )
        for item in report.get("checks", [])
        if isinstance(item, Mapping)
    ]


def build_signed_live_report(
    report: Mapping[str, Any],
    *,
    env: Mapping[str, str],
    current_release: Mapping[str, Any],
    max_age_minutes: int,
) -> dict[str, Any]:
    """Bind live evidence to the active release/configuration and sign it."""
    secret = require_signing_secret(env)
    created = utc_now()
    payload = dict(report)
    payload.update(
        {
            "schema_version": 1,
            "kind": "pilot_live_gate",
            "generated_at": utc_timestamp(created),
            "created_at": utc_timestamp(created),
            "expires_at": utc_timestamp(
                created + timedelta(minutes=max_age_minutes)
            ),
            "configuration_fingerprint": configuration_fingerprint(env, secret),
            "release": release_binding(current_release),
        }
    )
    return sign_payload(payload, secret)


def main() -> int:
    parser = argparse.ArgumentParser(description="FLASHIN pilot readiness gate")
    parser.add_argument(
        "--phase",
        choices=("predeploy", "live"),
        default="predeploy",
        help="predeploy validates launch inputs; live also probes public endpoints",
    )
    args = parser.parse_args()

    env = read_env(ROOT / ".env")
    checks = build_predeploy_checks(ROOT)
    checks.extend(_provider_wiring_checks(env))
    if args.phase == "live":
        checks.extend(build_live_checks(ROOT))

    report = build_report(args.phase, checks)
    if args.phase == "live":
        current_release = load_json(CURRENT_RELEASE_PATH)
        max_age_minutes = _positive_int(
            env, "PILOT_LIVE_GATE_MAX_AGE_MINUTES", 30
        )
        report = build_signed_live_report(
            report,
            env=env,
            current_release=current_release,
            max_age_minutes=max_age_minutes,
        )
    else:
        report["generated_at"] = utc_timestamp()
    stem = (
        "readiness_gate_report"
        if args.phase == "predeploy"
        else "pilot_live_gate_report"
    )
    json_path, markdown_path = write_report(ROOT, report, stem=stem)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(
        {
            "json": str(json_path.relative_to(ROOT)),
            "markdown": str(markdown_path.relative_to(ROOT)),
        }
    )
    return 0 if report["go"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
