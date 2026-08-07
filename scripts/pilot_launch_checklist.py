#!/usr/bin/env python3
"""Create and verify signed evidence for the final FLASHIN P01-P20 launch checklist."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Mapping, Sequence

from pilot_admission import load_verified_release_state
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

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = ROOT / "docs/pilot/live_pilot_runner.json"
DEFAULT_REPORT = ROOT / "docs/pilot/launch_checklist_report.json"
CURRENT_RELEASE_PATH = ROOT / "deploy/release/runtime/current_release.json"
EVIDENCE_DIRECTORY = Path("docs/pilot/evidence")
SCHEMA_VERSION = 1
MAX_EVIDENCE_FILE_BYTES = 20 * 1024 * 1024
RAW_TELEGRAM_MARKERS = ("auth_date=", "query_id=", "user=%7b", "hash=")
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

STEP_CONTRACT = (
    ("P01", "Open Mini App in Telegram", True),
    ("P02", "Open catalog", True),
    ("P03", "Open product card", True),
    ("P04", "Add product to cart", True),
    ("P05", "Apply promo code if available", False),
    ("P06", "Apply loyalty points if available", False),
    ("P07", "Apply referral code if available", False),
    ("P08", "Create checkout", True),
    ("P09", "Create YooKassa payment", True),
    ("P10", "Complete test payment", True),
    ("P11", "Verify payment webhook", True),
    ("P12", "Verify order paid", True),
    ("P13", "Verify stock writeoff/reservation", True),
    ("P14", "Verify fulfillment task", True),
    ("P15", "Create support ticket", False),
    ("P16", "Create refund request", True),
    ("P17", "Approve refund", True),
    ("P18", "Verify loyalty points returned", False),
    ("P19", "Verify admin audit trail", True),
    ("P20", "Verify customer notification", False),
)
STEP_BY_ID = {step_id: (title, critical) for step_id, title, critical in STEP_CONTRACT}


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


def _input_evidence_path(root: Path, raw: object) -> tuple[Path, str]:
    value = str(raw or "").strip()
    if not value:
        raise ValueError("evidence path is missing")
    source = Path(value).expanduser()
    if not source.is_absolute():
        source = root / source
    source = source.absolute()
    try:
        relative = source.relative_to(root.absolute())
    except ValueError as exc:
        raise ValueError("evidence file must be inside the pilot repository root") from exc
    required_root = EVIDENCE_DIRECTORY.parts
    if relative.parts[: len(required_root)] != required_root:
        raise ValueError("evidence file must be stored under docs/pilot/evidence")
    return source, relative.as_posix()


def _report_evidence_path(root: Path, raw: object) -> Path:
    value = str(raw or "").strip()
    if not value:
        return root / EVIDENCE_DIRECTORY / "__missing__"
    path = Path(value)
    if not path.is_absolute():
        return (root / path).absolute()
    if path.exists():
        return path
    return (root / EVIDENCE_DIRECTORY / path.name).absolute()


def _validate_evidence_file(
    path: Path,
    *,
    expected_sha256: str | None,
    env: Mapping[str, str],
) -> list[str]:
    errors: list[str] = []
    if path.is_symlink():
        errors.append(f"evidence file must not be a symlink: {path}")
    if not path.is_file():
        return list(dict.fromkeys(errors + [f"evidence file is missing: {path}"]))
    try:
        size = path.stat().st_size
    except OSError as exc:
        return list(dict.fromkeys(errors + [f"cannot stat evidence file {path}: {exc}"]))
    if size <= 0:
        errors.append(f"evidence file is empty: {path}")
    if size > MAX_EVIDENCE_FILE_BYTES:
        errors.append(f"evidence file exceeds {MAX_EVIDENCE_FILE_BYTES} bytes: {path}")
    actual_sha256 = sha256_file(path)
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
        if sum(marker.encode("ascii") in lowered for marker in RAW_TELEGRAM_MARKERS) >= 3:
            errors.append(f"evidence file appears to contain raw Telegram init data: {path}")
    return list(dict.fromkeys(errors))


def _validate_observed_at(
    raw: object,
    *,
    now: datetime,
    maximum_age: timedelta,
    label: str,
) -> list[str]:
    try:
        observed = parse_timestamp(raw, "observed_at")
    except ValueError as exc:
        return [f"{label}: {exc}"]
    errors: list[str] = []
    if observed > now + timedelta(minutes=5):
        errors.append(f"{label} observed_at is too far in the future")
    if now - observed > maximum_age:
        errors.append(f"{label} evidence is older than the allowed maximum age")
    return errors


def normalize_input_steps(
    payload: Mapping[str, Any],
    *,
    root: Path,
    env: Mapping[str, str],
    now: datetime,
    maximum_age: timedelta,
) -> tuple[list[dict[str, Any]], list[str]]:
    raw_steps = payload.get("steps")
    if not isinstance(raw_steps, list):
        return [], ["launch checklist steps are missing"]

    errors: list[str] = []
    if len(raw_steps) != len(STEP_CONTRACT):
        errors.append(f"launch checklist must contain exactly {len(STEP_CONTRACT)} steps")

    ids = [str(item.get("id", "")).strip() for item in raw_steps if isinstance(item, Mapping)]
    expected_ids = [item[0] for item in STEP_CONTRACT]
    if len(ids) != len(raw_steps):
        errors.append("every launch checklist step must be a JSON object")
    if ids != expected_ids:
        errors.append("launch checklist step order/IDs do not match the immutable P01-P20 contract")

    normalized: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_steps):
        if not isinstance(raw, Mapping):
            continue
        step_id = str(raw.get("id", "")).strip()
        expected = STEP_BY_ID.get(step_id)
        if expected is None:
            errors.append(f"unknown launch checklist step: {step_id or index + 1}")
            continue
        expected_title, expected_critical = expected
        title = str(raw.get("title", "")).strip()
        critical = raw.get("critical")
        if title != expected_title:
            errors.append(f"{step_id} title does not match the immutable contract")
        if critical is not expected_critical:
            errors.append(f"{step_id} critical flag does not match the immutable contract")

        status = str(raw.get("status", "")).strip().lower()
        owner = str(raw.get("owner", "")).strip()
        comment = str(raw.get("comment", "")).strip()
        label = step_id or f"step-{index + 1}"

        if expected_critical and status != "pass":
            errors.append(f"{label} is critical and must be pass")
        elif not expected_critical and status not in {"pass", "skip"}:
            errors.append(f"{label} optional status must be pass or skip")

        if not owner:
            errors.append(f"{label} owner is missing")
        elif len(owner) > 160:
            errors.append(f"{label} owner is too long")
        errors.extend(_sensitive_text_errors(owner, env, f"{label} owner"))
        errors.extend(_sensitive_text_errors(comment, env, f"{label} comment"))
        errors.extend(
            _validate_observed_at(
                raw.get("observed_at"),
                now=now,
                maximum_age=maximum_age,
                label=label,
            )
        )

        raw_evidence = raw.get("evidence")
        if status == "pass":
            if not isinstance(raw_evidence, list) or not raw_evidence:
                errors.append(f"{label} pass requires at least one evidence file")
                raw_evidence = []
        elif status == "skip":
            if expected_critical:
                errors.append(f"{label} critical step cannot be skipped")
            if len(comment) < 8:
                errors.append(f"{label} skip requires a meaningful comment")
            if raw_evidence is None:
                raw_evidence = []
            elif not isinstance(raw_evidence, list):
                errors.append(f"{label} evidence must be a list")
                raw_evidence = []
        else:
            raw_evidence = [] if raw_evidence is None else raw_evidence
            if not isinstance(raw_evidence, list):
                raw_evidence = []

        if len(raw_evidence) > 10:
            errors.append(f"{label} has too many evidence files")
            raw_evidence = raw_evidence[:10]

        normalized_evidence: list[dict[str, str]] = []
        for evidence_index, item in enumerate(raw_evidence, start=1):
            if not isinstance(item, Mapping):
                errors.append(f"{label} evidence #{evidence_index} must be an object")
                continue
            evidence_label = str(item.get("label", "")).strip()
            if not evidence_label:
                errors.append(f"{label} evidence #{evidence_index} label is missing")
            elif len(evidence_label) > 160:
                errors.append(f"{label} evidence #{evidence_index} label is too long")
            errors.extend(
                _sensitive_text_errors(
                    evidence_label, env, f"{label} evidence #{evidence_index} label"
                )
            )
            try:
                path, portable_path = _input_evidence_path(root, item.get("path"))
            except ValueError as exc:
                errors.append(f"{label} evidence #{evidence_index}: {exc}")
                continue
            errors.extend(_validate_evidence_file(path, expected_sha256=None, env=env))
            if path.is_file():
                normalized_evidence.append(
                    {
                        "label": evidence_label,
                        "path": portable_path,
                        "sha256": sha256_file(path),
                    }
                )

        normalized.append(
            {
                "id": step_id,
                "title": expected_title,
                "critical": expected_critical,
                "status": status,
                "observed_at": raw.get("observed_at"),
                "owner": owner,
                "comment": comment,
                "evidence": normalized_evidence,
            }
        )

    return normalized, list(dict.fromkeys(errors))


def build_report(
    payload: Mapping[str, Any],
    *,
    source_path: Path,
    root: Path,
    env: Mapping[str, str],
    current_release: Mapping[str, Any],
    max_age_hours: int,
    now: datetime | None = None,
) -> dict[str, Any]:
    secret = require_signing_secret(env)
    created = (now or utc_now()).astimezone(UTC)
    steps, errors = normalize_input_steps(
        payload,
        root=root,
        env=env,
        now=created,
        maximum_age=timedelta(hours=max_age_hours),
    )
    if errors:
        raise ValueError("; ".join(errors))
    source = source_path.absolute()
    try:
        source_relative = source.relative_to(root.absolute()).as_posix()
    except ValueError as exc:
        raise ValueError("launch checklist source must be inside the pilot repository root") from exc
    if source_relative != "docs/pilot/live_pilot_runner.json":
        raise ValueError("launch checklist source must be docs/pilot/live_pilot_runner.json")

    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "kind": "pilot_launch_checklist",
        "created_at": utc_timestamp(created),
        "expires_at": utc_timestamp(created + timedelta(hours=max_age_hours)),
        "configuration_fingerprint": configuration_fingerprint(env, secret),
        "release": release_binding(current_release),
        "go": True,
        "source": {"path": source_relative, "sha256": sha256_file(source)},
        "summary": {
            "total": len(steps),
            "passed": sum(item["status"] == "pass" for item in steps),
            "skipped": sum(item["status"] == "skip" for item in steps),
            "critical": sum(bool(item["critical"]) for item in steps),
        },
        "steps": steps,
    }
    return sign_payload(report, secret)


def _validate_report_steps(
    report: Mapping[str, Any],
    *,
    root: Path,
    env: Mapping[str, str],
    now: datetime,
    maximum_age: timedelta,
) -> list[str]:
    steps = report.get("steps")
    if not isinstance(steps, list):
        return ["launch checklist report steps are missing"]
    errors: list[str] = []
    if len(steps) != len(STEP_CONTRACT):
        errors.append(f"launch checklist report must contain exactly {len(STEP_CONTRACT)} steps")
    ids = [str(item.get("id", "")).strip() for item in steps if isinstance(item, Mapping)]
    if ids != [item[0] for item in STEP_CONTRACT]:
        errors.append("launch checklist report step order/IDs do not match P01-P20")

    for index, item in enumerate(steps):
        if not isinstance(item, Mapping):
            errors.append(f"launch checklist report step #{index + 1} is not an object")
            continue
        step_id = str(item.get("id", "")).strip()
        expected = STEP_BY_ID.get(step_id)
        if expected is None:
            errors.append(f"unknown launch checklist report step: {step_id or index + 1}")
            continue
        title, critical = expected
        if item.get("title") != title or item.get("critical") is not critical:
            errors.append(f"{step_id} report contract metadata is invalid")
        status = str(item.get("status", "")).strip().lower()
        if critical and status != "pass":
            errors.append(f"{step_id} critical report step is not pass")
        if not critical and status not in {"pass", "skip"}:
            errors.append(f"{step_id} optional report step is neither pass nor skip")
        owner = str(item.get("owner", "")).strip()
        comment = str(item.get("comment", "")).strip()
        if not owner:
            errors.append(f"{step_id} report owner is missing")
        errors.extend(_sensitive_text_errors(owner, env, f"{step_id} report owner"))
        errors.extend(_sensitive_text_errors(comment, env, f"{step_id} report comment"))
        errors.extend(
            _validate_observed_at(
                item.get("observed_at"),
                now=now,
                maximum_age=maximum_age,
                label=step_id,
            )
        )
        evidence = item.get("evidence")
        if status == "pass" and (not isinstance(evidence, list) or not evidence):
            errors.append(f"{step_id} pass report requires evidence")
            continue
        if status == "skip" and len(comment) < 8:
            errors.append(f"{step_id} skip report requires a meaningful comment")
        if not isinstance(evidence, list):
            evidence = []
        for evidence_index, entry in enumerate(evidence, start=1):
            if not isinstance(entry, Mapping):
                errors.append(f"{step_id} report evidence #{evidence_index} is not an object")
                continue
            evidence_label = str(entry.get("label", "")).strip()
            if not evidence_label:
                errors.append(f"{step_id} report evidence #{evidence_index} label is missing")
            errors.extend(
                _sensitive_text_errors(
                    evidence_label,
                    env,
                    f"{step_id} report evidence #{evidence_index} label",
                )
            )
            path = _report_evidence_path(root, entry.get("path"))
            expected_sha = str(entry.get("sha256", "")).strip()
            if len(expected_sha) != 64:
                errors.append(f"{step_id} report evidence #{evidence_index} SHA-256 is invalid")
                expected_sha = None
            errors.extend(_validate_evidence_file(path, expected_sha256=expected_sha, env=env))
    return list(dict.fromkeys(errors))


def validate_report(
    report: Mapping[str, Any],
    *,
    root: Path,
    env: Mapping[str, str],
    expected_release: Mapping[str, Any],
    max_age_hours: int | None = None,
    now: datetime | None = None,
) -> list[str]:
    current = (now or utc_now()).astimezone(UTC)
    age_hours = max_age_hours or _positive_int(
        env,
        "PILOT_LAUNCH_CHECKLIST_MAX_AGE_HOURS",
        24,
        1,
        72,
    )
    maximum_age = timedelta(hours=age_hours)
    errors: list[str] = []
    try:
        secret = require_signing_secret(env)
    except ValueError as exc:
        return [str(exc)]
    if report.get("schema_version") != SCHEMA_VERSION:
        errors.append("unsupported launch checklist evidence schema")
    if report.get("kind") != "pilot_launch_checklist":
        errors.append("launch checklist evidence kind is invalid")
    if report.get("go") is not True:
        errors.append("launch checklist evidence decision is not GO")
    if not verify_payload_signature(report, secret):
        errors.append("launch checklist evidence signature is invalid")
    if report.get("configuration_fingerprint") != configuration_fingerprint(env, secret):
        errors.append("launch checklist configuration fingerprint does not match")

    release = report.get("release")
    if not isinstance(release, Mapping):
        errors.append("launch checklist release binding is missing")
    else:
        errors.extend(
            f"launch checklist release: {item}"
            for item in validate_release_binding(release, expected_release)
        )
    errors.extend(validate_evidence_window(report, now=current, maximum_age=maximum_age))

    source = report.get("source")
    if not isinstance(source, Mapping):
        errors.append("launch checklist source binding is missing")
    else:
        source_path = str(source.get("path", "")).strip()
        expected_sha = str(source.get("sha256", "")).strip()
        if source_path != "docs/pilot/live_pilot_runner.json":
            errors.append("launch checklist source path is invalid")
        path = (root / source_path).absolute() if source_path else root / "__missing__"
        if not path.is_file():
            errors.append("launch checklist source file is missing")
        elif len(expected_sha) != 64:
            errors.append("launch checklist source SHA-256 is invalid")
        elif sha256_file(path) != expected_sha:
            errors.append("launch checklist source checksum does not match")

    errors.extend(
        _validate_report_steps(
            report,
            root=root,
            env=env,
            now=current,
            maximum_age=maximum_age,
        )
    )
    summary = report.get("summary")
    if not isinstance(summary, Mapping):
        errors.append("launch checklist summary is missing")
    else:
        expected_passed = sum(
            isinstance(item, Mapping) and item.get("status") == "pass"
            for item in report.get("steps", [])
        )
        expected_skipped = sum(
            isinstance(item, Mapping) and item.get("status") == "skip"
            for item in report.get("steps", [])
        )
        if summary.get("total") != len(STEP_CONTRACT):
            errors.append("launch checklist summary total is invalid")
        if summary.get("passed") != expected_passed or summary.get("skipped") != expected_skipped:
            errors.append("launch checklist summary counts do not match steps")
    return list(dict.fromkeys(errors))


def render_markdown(report: Mapping[str, Any]) -> str:
    lines = [
        "# FLASHIN P01-P20 launch checklist evidence",
        "",
        f"Decision: **{'GO' if report.get('go') is True else 'NO-GO'}**",
        "",
        f"Created: `{report.get('created_at', 'unknown')}`",
        f"Expires: `{report.get('expires_at', 'unknown')}`",
        "",
        "| Step | Critical | Status | Owner | Comment |",
        "|---|---:|---|---|---|",
    ]
    for item in report.get("steps", []):
        if not isinstance(item, Mapping):
            continue
        comment = str(item.get("comment", "")).replace("|", "\\|").replace("\n", " ")
        owner = str(item.get("owner", "")).replace("|", "\\|")
        lines.append(
            f"| {item.get('id')} — {item.get('title')} | "
            f"{'yes' if item.get('critical') else 'no'} | {item.get('status')} | {owner} | {comment} |"
        )
    lines.extend(
        [
            "",
            "All PASS evidence files are hashed and must stay under `docs/pilot/evidence`.",
            "Raw Telegram initData and provider secrets are forbidden in checklist evidence.",
            "",
        ]
    )
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("create", help="Create signed P01-P20 launch checklist evidence")
    create.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    create.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    verify = sub.add_parser("verify", help="Verify signed P01-P20 launch checklist evidence")
    verify.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    env = read_env(ROOT / ".env")
    try:
        current_release = load_verified_release_state(CURRENT_RELEASE_PATH)
        if args.command == "create":
            max_age_hours = _positive_int(
                env,
                "PILOT_LAUNCH_CHECKLIST_MAX_AGE_HOURS",
                24,
                1,
                72,
            )
            payload = load_json(args.input)
            report = build_report(
                payload,
                source_path=args.input,
                root=ROOT,
                env=env,
                current_release=current_release,
                max_age_hours=max_age_hours,
            )
            atomic_write_json(args.report, report)
            atomic_write_text(args.report.with_suffix(".md"), render_markdown(report))
            print(json.dumps({"go": True, "report": str(args.report)}, ensure_ascii=False))
            return 0
        report = load_json(args.report)
        errors = validate_report(
            report,
            root=ROOT,
            env=env,
            expected_release=current_release,
        )
        print(json.dumps({"go": not errors, "errors": errors}, ensure_ascii=False))
        return 0 if not errors else 1
    except (OSError, ValueError) as exc:
        print(json.dumps({"go": False, "errors": [str(exc)]}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
