#!/usr/bin/env python3
"""Create and verify signed evidence for FLASHIN's deployed pilot lifecycle.

Provider reachability is necessary but not sufficient for a real-money pilot.
This gate binds named, file-backed observations of the deployed customer and
operator lifecycle to one exact release and production configuration.
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Mapping, Sequence

from pilot_evidence import (
    atomic_write_json,
    atomic_write_text,
    configuration_fingerprint,
    load_json,
    parse_timestamp,
    release_binding,
    require_signing_secret,
    sha256_file,
    sign_payload,
    utc_now,
    utc_timestamp,
    validate_evidence_window,
    validate_release_binding,
    verify_payload_signature,
)
from pilot_readiness import read_env
from release_control import verify_release

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = ROOT / "docs/pilot/live_lifecycle_input.json"
DEFAULT_REPORT = ROOT / "docs/pilot/live_lifecycle_report.json"
CURRENT_RELEASE_PATH = ROOT / "deploy/release/runtime/current_release.json"
SCHEMA_VERSION = 1
MAX_EVIDENCE_FILE_BYTES = 20 * 1024 * 1024
BASE_REQUIRED_SCENARIOS = (
    "telegram_real_auth",
    "yookassa_payment_redirect",
    "yookassa_payment_return",
    "yookassa_duplicate_webhook",
    "yookassa_refund",
    "moysklad_live_sync",
    "notification_delivery",
)
CONDITIONAL_SCENARIOS = {
    "meilisearch_live_index",
    "media_live_delivery",
}
SECRET_ENV_KEYS = (
    "TELEGRAM_BOT_TOKEN",
    "BOT_TOKEN",
    "YOOKASSA_SECRET_KEY",
    "MOYSKLAD_TOKEN",
    "MOYSKLAD_PASSWORD",
    "S3_ACCESS_KEY_ID",
    "S3_SECRET_ACCESS_KEY",
    "MEILISEARCH_MASTER_KEY",
    "PILOT_EVIDENCE_SIGNING_SECRET",
)
RAW_TELEGRAM_MARKERS = ("auth_date=", "query_id=", "user=%7b", "hash=")


def _true(value: object) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _positive_int(
    env: Mapping[str, str],
    key: str,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    raw = str(env.get(key, default)).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{key} must be an integer") from exc
    if value < minimum or value > maximum:
        raise ValueError(f"{key} must be between {minimum} and {maximum}")
    return value


def required_scenarios(env: Mapping[str, str]) -> tuple[str, ...]:
    required = set(BASE_REQUIRED_SCENARIOS)
    if _true(env.get("MEILISEARCH_ENABLED")):
        required.add("meilisearch_live_index")
    if str(env.get("MEDIA_STORAGE", "local")).strip().lower() in {"s3", "r2"}:
        required.add("media_live_delivery")
    return tuple(sorted(required))


def _secret_values(env: Mapping[str, str]) -> tuple[str, ...]:
    return tuple(
        sorted(
            {
                str(env.get(key, "")).strip()
                for key in SECRET_ENV_KEYS
                if len(str(env.get(key, "")).strip()) >= 4
            },
            key=len,
            reverse=True,
        )
    )


def _sensitive_text_errors(text: str, env: Mapping[str, str], label: str) -> list[str]:
    lowered = text.lower()
    errors: list[str] = []
    for marker in RAW_TELEGRAM_MARKERS:
        if marker in lowered:
            errors.append(f"{label} contains raw Telegram init data marker: {marker}")
    for secret in _secret_values(env):
        if secret and secret in text:
            errors.append(f"{label} contains a configured secret")
            break
    return errors


def _resolve_evidence_path(root: Path, raw: object) -> Path:
    value = str(raw or "").strip()
    if not value:
        raise ValueError("evidence path is missing")
    path = Path(value)
    if not path.is_absolute():
        path = root / path
    return path.resolve()


def _validate_evidence_file(
    path: Path,
    *,
    expected_sha256: str | None,
    env: Mapping[str, str],
) -> list[str]:
    errors: list[str] = []
    if not path.is_file():
        return [f"evidence file is missing: {path}"]
    if path.is_symlink():
        errors.append(f"evidence file must not be a symlink: {path}")
    try:
        size = path.stat().st_size
    except OSError as exc:
        return [f"cannot stat evidence file {path}: {exc}"]
    if size <= 0:
        errors.append(f"evidence file is empty: {path}")
    if size > MAX_EVIDENCE_FILE_BYTES:
        errors.append(
            f"evidence file exceeds {MAX_EVIDENCE_FILE_BYTES} bytes: {path}"
        )
    actual_sha256 = sha256_file(path) if path.is_file() else ""
    if expected_sha256 is not None and actual_sha256 != expected_sha256:
        errors.append(f"evidence checksum does not match: {path}")

    try:
        with path.open("rb") as handle:
            sample = handle.read(1_048_576)
    except OSError as exc:
        errors.append(f"cannot read evidence file {path}: {exc}")
    else:
        for secret in _secret_values(env):
            encoded = secret.encode("utf-8", errors="ignore")
            if encoded and encoded in sample:
                errors.append(f"evidence file contains a configured secret: {path}")
                break
        lowered = sample.lower()
        telegram_markers = sum(
            marker.encode("ascii") in lowered for marker in RAW_TELEGRAM_MARKERS
        )
        if telegram_markers >= 3:
            errors.append(f"evidence file appears to contain raw Telegram init data: {path}")
    return errors


def _validate_scenario_common(
    scenario: Mapping[str, Any],
    *,
    env: Mapping[str, str],
    now: datetime,
    maximum_age: timedelta,
) -> list[str]:
    name = str(scenario.get("name", "")).strip()
    errors: list[str] = []
    if scenario.get("status") != "PASS":
        errors.append(f"scenario {name or 'unknown'} status must be PASS")
    owner = str(scenario.get("owner", "")).strip()
    if not owner:
        errors.append(f"scenario {name or 'unknown'} owner is missing")
    elif len(owner) > 160:
        errors.append(f"scenario {name} owner is too long")
    subject_id = str(scenario.get("subject_id", "")).strip()
    if not subject_id:
        errors.append(f"scenario {name or 'unknown'} subject_id is missing")
    elif len(subject_id) > 255:
        errors.append(f"scenario {name} subject_id is too long")
    notes = str(scenario.get("notes", "")).strip()
    if len(notes) > 2000:
        errors.append(f"scenario {name} notes are too long")
    errors.extend(_sensitive_text_errors(owner, env, f"scenario {name} owner"))
    errors.extend(_sensitive_text_errors(subject_id, env, f"scenario {name} subject_id"))
    errors.extend(_sensitive_text_errors(notes, env, f"scenario {name} notes"))
    try:
        observed = parse_timestamp(scenario.get("observed_at"), "observed_at")
    except ValueError as exc:
        errors.append(f"scenario {name}: {exc}")
    else:
        if observed > now + timedelta(minutes=5):
            errors.append(f"scenario {name} observed_at is too far in the future")
        if now - observed > maximum_age:
            errors.append(f"scenario {name} evidence is older than the allowed maximum age")
    return errors


def _normalize_input_scenarios(
    payload: Mapping[str, Any],
    *,
    root: Path,
    env: Mapping[str, str],
    now: datetime,
    maximum_age: timedelta,
) -> tuple[list[dict[str, Any]], list[str]]:
    raw_scenarios = payload.get("scenarios")
    if not isinstance(raw_scenarios, list):
        return [], ["lifecycle input scenarios are missing"]
    names = [
        str(item.get("name", "")).strip()
        for item in raw_scenarios
        if isinstance(item, Mapping)
    ]
    required = set(required_scenarios(env))
    actual = set(names)
    errors: list[str] = []
    if len(names) != len(raw_scenarios):
        errors.append("every lifecycle scenario must be a JSON object")
    duplicates = sorted({name for name in names if names.count(name) > 1})
    if duplicates:
        errors.append("duplicate lifecycle scenarios: " + ", ".join(duplicates))
    missing = sorted(required - actual)
    unknown = sorted(actual - required)
    if missing:
        errors.append("missing lifecycle scenarios: " + ", ".join(missing))
    if unknown:
        errors.append("unknown lifecycle scenarios: " + ", ".join(unknown))

    normalized: list[dict[str, Any]] = []
    for raw in raw_scenarios:
        if not isinstance(raw, Mapping):
            continue
        name = str(raw.get("name", "")).strip()
        errors.extend(
            _validate_scenario_common(
                raw,
                env=env,
                now=now,
                maximum_age=maximum_age,
            )
        )
        evidence = raw.get("evidence")
        if not isinstance(evidence, list) or not evidence:
            errors.append(f"scenario {name or 'unknown'} requires at least one evidence file")
            continue
        if len(evidence) > 10:
            errors.append(f"scenario {name} has too many evidence files")
            continue
        normalized_evidence: list[dict[str, str]] = []
        for index, item in enumerate(evidence, start=1):
            if not isinstance(item, Mapping):
                errors.append(f"scenario {name} evidence #{index} must be an object")
                continue
            label = str(item.get("label", "")).strip()
            if not label:
                errors.append(f"scenario {name} evidence #{index} label is missing")
            elif len(label) > 160:
                errors.append(f"scenario {name} evidence #{index} label is too long")
            errors.extend(
                _sensitive_text_errors(label, env, f"scenario {name} evidence label")
            )
            try:
                path = _resolve_evidence_path(root, item.get("path"))
            except ValueError as exc:
                errors.append(f"scenario {name} evidence #{index}: {exc}")
                continue
            errors.extend(
                _validate_evidence_file(
                    path,
                    expected_sha256=None,
                    env=env,
                )
            )
            if path.is_file():
                normalized_evidence.append(
                    {
                        "label": label,
                        "path": str(path),
                        "sha256": sha256_file(path),
                    }
                )
        normalized.append(
            {
                "name": name,
                "status": raw.get("status"),
                "observed_at": raw.get("observed_at"),
                "owner": str(raw.get("owner", "")).strip(),
                "subject_id": str(raw.get("subject_id", "")).strip(),
                "notes": str(raw.get("notes", "")).strip(),
                "evidence": normalized_evidence,
            }
        )
    return sorted(normalized, key=lambda item: item["name"]), list(dict.fromkeys(errors))


def build_report(
    payload: Mapping[str, Any],
    *,
    root: Path,
    env: Mapping[str, str],
    current_release: Mapping[str, Any],
    max_age_hours: int,
    now: datetime | None = None,
) -> dict[str, Any]:
    secret = require_signing_secret(env)
    created = (now or utc_now()).astimezone(UTC)
    scenarios, errors = _normalize_input_scenarios(
        payload,
        root=root,
        env=env,
        now=created,
        maximum_age=timedelta(hours=max_age_hours),
    )
    if errors:
        raise ValueError("; ".join(errors))
    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "kind": "pilot_live_lifecycle",
        "created_at": utc_timestamp(created),
        "expires_at": utc_timestamp(created + timedelta(hours=max_age_hours)),
        "configuration_fingerprint": configuration_fingerprint(env, secret),
        "release": release_binding(current_release),
        "go": True,
        "summary": {
            "required": len(required_scenarios(env)),
            "passed": len(scenarios),
        },
        "scenarios": scenarios,
    }
    return sign_payload(report, secret)


def validate_live_lifecycle_report(
    report: Mapping[str, Any],
    *,
    root: Path,
    env: Mapping[str, str],
    expected_release: Mapping[str, Any],
    max_age_hours: int | None = None,
    now: datetime | None = None,
) -> list[str]:
    errors: list[str] = []
    current = (now or utc_now()).astimezone(UTC)
    try:
        secret = require_signing_secret(env)
    except ValueError as exc:
        return [str(exc)]
    if report.get("schema_version") != SCHEMA_VERSION:
        errors.append("unsupported live lifecycle evidence schema")
    if report.get("kind") != "pilot_live_lifecycle":
        errors.append("live lifecycle evidence kind is invalid")
    if report.get("go") is not True:
        errors.append("live lifecycle evidence decision is not GO")
    if not verify_payload_signature(report, secret):
        errors.append("live lifecycle evidence signature is invalid")
    if report.get("configuration_fingerprint") != configuration_fingerprint(env, secret):
        errors.append("live lifecycle configuration fingerprint does not match")
    binding = report.get("release")
    if not isinstance(binding, Mapping):
        errors.append("live lifecycle release binding is missing")
    else:
        errors.extend(
            f"live lifecycle release: {item}"
            for item in validate_release_binding(binding, expected_release)
        )
    age_hours = max_age_hours or _positive_int(
        env,
        "PILOT_LIFECYCLE_EVIDENCE_MAX_AGE_HOURS",
        24,
        1,
        168,
    )
    errors.extend(
        validate_evidence_window(
            report,
            now=current,
            maximum_age=timedelta(hours=age_hours),
        )
    )

    scenarios = report.get("scenarios")
    if not isinstance(scenarios, list):
        return list(dict.fromkeys(errors + ["live lifecycle scenarios are missing"]))
    names = [
        str(item.get("name", "")).strip()
        for item in scenarios
        if isinstance(item, Mapping)
    ]
    required = set(required_scenarios(env))
    actual = set(names)
    duplicates = sorted({name for name in names if names.count(name) > 1})
    if len(names) != len(scenarios):
        errors.append("every live lifecycle scenario must be an object")
    if duplicates:
        errors.append("duplicate live lifecycle scenarios: " + ", ".join(duplicates))
    missing = sorted(required - actual)
    unknown = sorted(actual - required)
    if missing:
        errors.append("missing live lifecycle scenarios: " + ", ".join(missing))
    if unknown:
        errors.append("unknown live lifecycle scenarios: " + ", ".join(unknown))

    for scenario in scenarios:
        if not isinstance(scenario, Mapping):
            continue
        name = str(scenario.get("name", "")).strip()
        errors.extend(
            _validate_scenario_common(
                scenario,
                env=env,
                now=current,
                maximum_age=timedelta(hours=age_hours),
            )
        )
        evidence = scenario.get("evidence")
        if not isinstance(evidence, list) or not evidence:
            errors.append(f"scenario {name or 'unknown'} requires evidence files")
            continue
        for index, item in enumerate(evidence, start=1):
            if not isinstance(item, Mapping):
                errors.append(f"scenario {name} evidence #{index} must be an object")
                continue
            label = str(item.get("label", "")).strip()
            if not label:
                errors.append(f"scenario {name} evidence #{index} label is missing")
            errors.extend(
                _sensitive_text_errors(label, env, f"scenario {name} evidence label")
            )
            try:
                path = _resolve_evidence_path(root, item.get("path"))
            except ValueError as exc:
                errors.append(f"scenario {name} evidence #{index}: {exc}")
                continue
            expected_sha256 = str(item.get("sha256", "")).strip()
            if len(expected_sha256) != 64:
                errors.append(f"scenario {name} evidence #{index} SHA-256 is invalid")
            errors.extend(
                _validate_evidence_file(
                    path,
                    expected_sha256=expected_sha256,
                    env=env,
                )
            )

    summary = report.get("summary")
    if not isinstance(summary, Mapping):
        errors.append("live lifecycle summary is missing")
    else:
        if summary.get("required") != len(required):
            errors.append("live lifecycle required scenario count does not match")
        if summary.get("passed") != len(scenarios):
            errors.append("live lifecycle passed scenario count does not match")
    return list(dict.fromkeys(errors))


def render_markdown(report: Mapping[str, Any]) -> str:
    release = report.get("release") if isinstance(report.get("release"), Mapping) else {}
    lines = [
        "# FLASHIN live lifecycle evidence",
        "",
        f"Decision: **{'GO' if report.get('go') else 'NO-GO'}**",
        "",
        f"Created: `{report.get('created_at')}`",
        f"Expires: `{report.get('expires_at')}`",
        f"Release: `{release.get('release_id', 'unknown')}` / `{release.get('git_commit', 'unknown')}`",
        "",
    ]
    for scenario in report.get("scenarios", []):
        if not isinstance(scenario, Mapping):
            continue
        lines.append(
            f"- [{'x' if scenario.get('status') == 'PASS' else ' '}] "
            f"`{scenario.get('name')}` — owner: {scenario.get('owner')}; "
            f"subject: `{scenario.get('subject_id')}`; observed: `{scenario.get('observed_at')}`"
        )
    lines.extend(
        [
            "",
            "The report stores only identifiers, checksums and bounded notes. Raw Telegram initData and provider secrets are forbidden.",
            "",
        ]
    )
    return "\n".join(lines)


def _load_current_release(root: Path) -> dict[str, Any]:
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
            raise ValueError(f"Current release pointer {key} does not match archive")
    return state


def _write_report(path: Path, report: Mapping[str, Any]) -> tuple[Path, Path]:
    atomic_write_json(path, report)
    markdown = path.with_suffix(".md")
    atomic_write_text(markdown, render_markdown(report))
    return path, markdown


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("create", help="Create signed live lifecycle evidence")
    create.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    create.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    create.add_argument("--max-age-hours", type=int)
    verify = sub.add_parser("verify", help="Verify signed live lifecycle evidence")
    verify.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    verify.add_argument("--max-age-hours", type=int)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    env = read_env(ROOT / ".env")
    try:
        current_release = _load_current_release(ROOT)
        max_age_hours = args.max_age_hours or _positive_int(
            env,
            "PILOT_LIFECYCLE_EVIDENCE_MAX_AGE_HOURS",
            24,
            1,
            168,
        )
        if max_age_hours < 1 or max_age_hours > 168:
            raise ValueError("max age must be between 1 and 168 hours")
        if args.command == "create":
            report = build_report(
                load_json(args.input),
                root=ROOT,
                env=env,
                current_release=current_release,
                max_age_hours=max_age_hours,
            )
            json_path, markdown_path = _write_report(args.report, report)
            print(
                json.dumps(
                    {
                        "go": True,
                        "json": str(json_path),
                        "markdown": str(markdown_path),
                        "summary": report["summary"],
                    },
                    ensure_ascii=False,
                )
            )
            return 0
        report = load_json(args.report)
        errors = validate_live_lifecycle_report(
            report,
            root=ROOT,
            env=env,
            expected_release=current_release,
            max_age_hours=max_age_hours,
        )
        print(json.dumps({"go": not errors, "errors": errors}, ensure_ascii=False))
        return 0 if not errors else 1
    except (OSError, ValueError) as exc:
        print(json.dumps({"go": False, "errors": [str(exc)]}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
