#!/usr/bin/env python3
"""Run or verify signed live external-provider evidence for FLASHIN pilot admission."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Mapping, Sequence

from pilot_evidence import (
    atomic_write_text,
    configuration_fingerprint,
    load_json,
    release_binding,
    require_signing_secret,
    sign_payload,
    utc_now,
    utc_timestamp,
    validate_provider_report,
)
from pilot_readiness import read_env
from release_control import verify_release

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPORT = ROOT / "docs/pilot/integration_check_report.json"
DEFAULT_MAX_AGE_MINUTES = 60
SECRET_KEYS = {
    "TELEGRAM_BOT_TOKEN",
    "BOT_TOKEN",
    "YOOKASSA_SECRET_KEY",
    "MOYSKLAD_TOKEN",
    "MOYSKLAD_PASSWORD",
    "S3_ACCESS_KEY_ID",
    "S3_SECRET_ACCESS_KEY",
    "MEILISEARCH_MASTER_KEY",
    "PILOT_EVIDENCE_SIGNING_SECRET",
}


@dataclass(frozen=True)
class Probe:
    name: str
    script: str
    timeout: int = 60
    condition: str | None = None


PROBES = (
    Probe("telegram", "check_telegram_bot.py", 30),
    Probe("yookassa", "check_yookassa_test.py", 45),
    Probe("moysklad", "check_moysklad.py", 45),
    Probe("r2_s3", "check_r2_s3.py", 60, condition="durable_media"),
    Probe("meilisearch", "check_meilisearch.py", 30, condition="search_enabled"),
)


def build_probe_plan(env: Mapping[str, str]) -> list[dict[str, Any]]:
    media_storage = str(env.get("MEDIA_STORAGE", "local")).strip().lower()
    search_enabled = str(env.get("MEILISEARCH_ENABLED", "")).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    plan: list[dict[str, Any]] = []
    for probe in PROBES:
        enabled = True
        reason = "required for live pilot"
        if probe.condition == "durable_media":
            enabled = media_storage in {"s3", "r2"}
            reason = (
                f"MEDIA_STORAGE={media_storage or 'missing'}"
                if enabled
                else "durable media storage is not configured"
            )
        elif probe.condition == "search_enabled":
            enabled = search_enabled
            reason = "MEILISEARCH_ENABLED=true" if enabled else "search is disabled"
        plan.append({"probe": probe, "enabled": enabled, "reason": reason})
    return plan


def _redaction_values(env: Mapping[str, str]) -> list[str]:
    values = []
    for key in SECRET_KEYS:
        value = str(env.get(key, "")).strip()
        if len(value) >= 4:
            values.append(value)
    return sorted(set(values), key=len, reverse=True)


def redact(text: str, env: Mapping[str, str]) -> str:
    result = text
    for secret in _redaction_values(env):
        result = result.replace(secret, "<redacted>")
    return result


def build_probe_context(env: Mapping[str, str], release: Mapping[str, Any], run_id: str) -> dict[str, str]:
    shop_id = str(env.get("YOOKASSA_SHOP_ID", "")).strip()
    git_commit = str(release.get("git_commit", "")).strip()
    return_url = str(env.get("YOOKASSA_RETURN_URL", "")).strip()
    idempotence_key = str(
        uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"flashin:pilot-provider-probe:yookassa:{shop_id}:{git_commit}:{return_url}",
        )
    )
    return {
        "FLASHIN_PROBE_RUN_ID": run_id,
        "FLASHIN_RELEASE_GIT_COMMIT": git_commit,
        "FLASHIN_YOOKASSA_IDEMPOTENCE_KEY": idempotence_key,
    }


def _command(
    probe: Probe,
    *,
    host_python: bool,
    probe_context: Mapping[str, str] | None = None,
) -> list[str]:
    script_path = f"scripts/{probe.script}"
    if host_python:
        return [sys.executable, script_path]
    command = ["docker", "compose", "exec", "-T"]
    for key, value in sorted((probe_context or {}).items()):
        command.extend(["-e", f"{key}={value}"])
    command.extend(["backend", "python", script_path])
    return command


def run_probe(
    probe: Probe,
    *,
    env: Mapping[str, str],
    host_python: bool,
    probe_context: Mapping[str, str] | None = None,
    runner=subprocess.run,
) -> dict[str, Any]:
    command = _command(probe, host_python=host_python, probe_context=probe_context)
    process_env = dict(os.environ)
    if host_python:
        process_env.update(env)
        process_env.update(probe_context or {})
    try:
        completed = runner(
            command,
            cwd=ROOT,
            env=process_env,
            capture_output=True,
            text=True,
            timeout=probe.timeout,
            check=False,
        )
        stdout = redact((completed.stdout or "").strip(), env)[-4000:]
        stderr = redact((completed.stderr or "").strip(), env)[-4000:]
        return {
            "name": probe.name,
            "ok": completed.returncode == 0,
            "returncode": completed.returncode,
            "stdout": stdout,
            "stderr": stderr,
        }
    except (OSError, subprocess.SubprocessError) as exc:
        return {
            "name": probe.name,
            "ok": False,
            "returncode": None,
            "stdout": "",
            "stderr": redact(f"{exc.__class__.__name__}: {exc}", env),
        }


def load_current_release(root: Path = ROOT) -> dict[str, Any]:
    state = load_json(root / "deploy/release/runtime/current_release.json")
    archive = Path(str(state.get("archive", "")))
    verification = verify_release(archive)
    if not verification.get("ok"):
        raise ValueError(
            "Current release verification failed: "
            + "; ".join(str(item) for item in verification.get("errors", []))
        )
    for key in ("release_id", "git_commit", "sha256"):
        if str(state.get(key, "")) != str(verification.get(key, "")):
            raise ValueError(f"Current release pointer {key} does not match the archive")
    return state


def build_report(
    results: Sequence[Mapping[str, Any]],
    *,
    strict: bool,
    host_python: bool,
    env: Mapping[str, str],
    current_release: Mapping[str, Any],
    max_age_minutes: int,
    run_id: str,
    created_at: datetime | None = None,
) -> dict[str, Any]:
    secret = require_signing_secret(env)
    created = (created_at or utc_now()).astimezone(UTC)
    failed = [result for result in results if not result.get("ok")]
    payload: dict[str, Any] = {
        "schema_version": 1,
        "kind": "provider_probes",
        "created_at": utc_timestamp(created),
        "expires_at": utc_timestamp(created + timedelta(minutes=max_age_minutes)),
        "max_age_minutes": max_age_minutes,
        "run_id": run_id,
        "mode": "strict" if strict else "advisory",
        "execution": "host-python" if host_python else "backend-container",
        "release": release_binding(current_release),
        "configuration_fingerprint": configuration_fingerprint(env, secret),
        "go": not failed if strict else True,
        "summary": {
            "total": len(results),
            "passed": sum(1 for result in results if result.get("ok")),
            "failed": len(failed),
        },
        "side_effects": {
            "yookassa": "1.00 RUB pending test payment; idempotent per release and return URL",
        },
        "results": list(results),
    }
    return sign_payload(payload, secret)


def write_report(path: Path, report: Mapping[str, Any]) -> tuple[Path, Path]:
    json_path = path
    markdown_path = path.with_suffix(".md")
    atomic_write_text(json_path, json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    release = report.get("release") if isinstance(report.get("release"), Mapping) else {}
    lines = [
        "# FLASHIN provider integration evidence",
        "",
        f"Decision: **{'GO' if report.get('go') else 'NO-GO'}**",
        "",
        f"Created: `{report.get('created_at')}`",
        f"Expires: `{report.get('expires_at')}`",
        f"Release: `{release.get('release_id', 'unknown')}` / `{release.get('git_commit', 'unknown')}`",
        "",
        "YooKassa probe creates one 1.00 RUB pending payment idempotently per release and return URL.",
        "",
    ]
    for result in report.get("results", []):
        lines.append(
            f"- [{'x' if result.get('ok') else ' '}] `{result.get('name')}` — "
            f"exit={result.get('returncode')}"
        )
    atomic_write_text(markdown_path, "\n".join(lines) + "\n")
    return json_path, markdown_path


def verify_existing_report(
    path: Path,
    *,
    env: Mapping[str, str],
    current_release: Mapping[str, Any],
    max_age_minutes: int,
) -> tuple[dict[str, Any] | None, list[str]]:
    try:
        report = load_json(path)
    except ValueError as exc:
        return None, [str(exc)]
    errors = validate_provider_report(
        report,
        env=env,
        current_release=current_release,
        max_age_minutes=max_age_minutes,
    )
    return report, errors


def _safe_summary(report: Mapping[str, Any] | None, errors: Sequence[str]) -> dict[str, Any]:
    return {
        "go": not errors and bool(report and report.get("go")),
        "created_at": report.get("created_at") if report else None,
        "expires_at": report.get("expires_at") if report else None,
        "release": report.get("release") if report else None,
        "summary": report.get("summary") if report else None,
        "errors": list(errors),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command")

    run = subparsers.add_parser("run", help="Run live probes and create signed evidence")
    run.add_argument(
        "--acknowledge-side-effects",
        action="store_true",
        help="Acknowledge that the YooKassa probe creates a 1.00 RUB pending payment",
    )
    run.add_argument("--force", action="store_true", help="Re-run even if valid fresh evidence exists")
    run.add_argument("--advisory", action="store_true")
    run.add_argument("--host-python", action="store_true")
    run.add_argument("--report", default=str(DEFAULT_REPORT))
    run.add_argument("--max-age-minutes", type=int)
    run.add_argument("--quiet", action="store_true")
    run.add_argument("--verbose", action="store_true")

    verify = subparsers.add_parser("verify", help="Verify existing signed evidence without side effects")
    verify.add_argument("--report", default=str(DEFAULT_REPORT))
    verify.add_argument("--max-age-minutes", type=int)
    verify.add_argument("--quiet", action="store_true")
    verify.add_argument("--verbose", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    raw_args = list(sys.argv[1:] if argv is None else argv)
    if not raw_args or raw_args[0].startswith("-"):
        raw_args.insert(0, "verify")
    args = build_parser().parse_args(raw_args)

    env = read_env(ROOT / ".env")
    try:
        require_signing_secret(env)
        current_release = load_current_release(ROOT)
    except ValueError as exc:
        print(json.dumps({"go": False, "errors": [str(exc)]}, ensure_ascii=False))
        return 1

    report_path = Path(args.report)
    configured_max_age = str(
        env.get("PILOT_PROVIDER_EVIDENCE_MAX_AGE_MINUTES", DEFAULT_MAX_AGE_MINUTES)
    ).strip()
    try:
        max_age_minutes = int(args.max_age_minutes or configured_max_age)
    except ValueError:
        print(json.dumps({"go": False, "errors": ["provider evidence max age must be an integer"]}))
        return 1
    if max_age_minutes < 5 or max_age_minutes > 240:
        print(json.dumps({"go": False, "errors": ["max age must be between 5 and 240 minutes"]}))
        return 1

    if args.command == "verify":
        report, errors = verify_existing_report(
            report_path,
            env=env,
            current_release=current_release,
            max_age_minutes=max_age_minutes,
        )
        summary = _safe_summary(report, errors)
        if args.verbose and report is not None and not args.quiet:
            print(json.dumps({"verification": summary, "report": report}, ensure_ascii=False, indent=2))
        else:
            print(json.dumps(summary, ensure_ascii=False))
        return 0 if summary["go"] else 1

    existing, existing_errors = verify_existing_report(
        report_path,
        env=env,
        current_release=current_release,
        max_age_minutes=max_age_minutes,
    )
    if existing is not None and not existing_errors and not args.force:
        summary = _safe_summary(existing, [])
        summary["reused"] = True
        print(json.dumps(summary, ensure_ascii=False))
        return 0
    if not args.acknowledge_side_effects:
        print(
            json.dumps(
                {
                    "go": False,
                    "errors": [
                        "Use run --acknowledge-side-effects: YooKassa creates a 1.00 RUB pending payment"
                    ],
                },
                ensure_ascii=False,
            )
        )
        return 1

    strict = not args.advisory
    run_id = uuid.uuid4().hex
    probe_context = build_probe_context(env, current_release, run_id)
    results: list[dict[str, Any]] = []
    for item in build_probe_plan(env):
        probe = item["probe"]
        if not item["enabled"]:
            results.append(
                {
                    "name": probe.name,
                    "ok": False,
                    "returncode": None,
                    "stdout": "",
                    "stderr": item["reason"],
                }
            )
            continue
        results.append(
            run_probe(
                probe,
                env=env,
                host_python=args.host_python,
                probe_context=probe_context,
            )
        )

    report = build_report(
        results,
        strict=strict,
        host_python=args.host_python,
        env=env,
        current_release=current_release,
        max_age_minutes=max_age_minutes,
        run_id=run_id,
    )
    json_path, markdown_path = write_report(report_path, report)
    if args.verbose and not args.quiet:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        print({"json": str(json_path), "markdown": str(markdown_path)})
    else:
        print(
            json.dumps(
                _safe_summary(report, [] if report["go"] else ["one or more probes failed"]),
                ensure_ascii=False,
            )
        )
    return 0 if report["go"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
