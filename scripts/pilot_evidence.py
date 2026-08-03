#!/usr/bin/env python3
"""Shared signed evidence primitives for FLASHIN pilot admission."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Mapping

SCHEMA_VERSION = 1
SIGNATURE_ALGORITHM = "hmac-sha256"
EXPECTED_PROVIDER_PROBES = {
    "telegram",
    "yookassa",
    "moysklad",
    "r2_s3",
    "meilisearch",
}
CONFIG_FINGERPRINT_KEYS = (
    "APP_ENV",
    "API_PUBLIC_URL",
    "MINI_APP_URL",
    "ADMIN_URL",
    "TELEGRAM_BOT_TOKEN",
    "YOOKASSA_SHOP_ID",
    "YOOKASSA_SECRET_KEY",
    "YOOKASSA_RETURN_URL",
    "MOYSKLAD_BASE_URL",
    "MOYSKLAD_TOKEN",
    "MOYSKLAD_LOGIN",
    "MOYSKLAD_PASSWORD",
    "MOYSKLAD_SALE_PRICE_TYPE",
    "MOYSKLAD_SIZE_ATTRIBUTE_NAMES",
    "MOYSKLAD_COLOR_ATTRIBUTE_NAMES",
    "MEDIA_STORAGE",
    "MEDIA_PUBLIC_BASE_URL",
    "S3_ENDPOINT_URL",
    "S3_BUCKET",
    "S3_REGION",
    "S3_ACCESS_KEY_ID",
    "S3_SECRET_ACCESS_KEY",
    "MEILISEARCH_ENABLED",
    "MEILISEARCH_URL",
    "MEILISEARCH_MASTER_KEY",
    "MEILISEARCH_PRODUCTS_INDEX",
)


def utc_now() -> datetime:
    return datetime.now(UTC)


def utc_timestamp(value: datetime | None = None) -> str:
    current = value or utc_now()
    if current.tzinfo is None:
        raise ValueError("Timestamp must be timezone-aware")
    return current.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_timestamp(value: Any, field: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} is missing")
    normalized = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError(f"{field} is not RFC 3339: {exc}") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{field} must contain a timezone")
    return parsed.astimezone(UTC)


def canonical_json_bytes(payload: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def _unsigned_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    unsigned = dict(payload)
    unsigned.pop("signature", None)
    return unsigned


def require_signing_secret(env: Mapping[str, str]) -> str:
    secret = str(env.get("PILOT_EVIDENCE_SIGNING_SECRET", "")).strip()
    if len(secret) < 32:
        raise ValueError("PILOT_EVIDENCE_SIGNING_SECRET must contain at least 32 characters")
    return secret


def sign_payload(payload: Mapping[str, Any], secret: str) -> dict[str, Any]:
    signed = _unsigned_payload(payload)
    digest = hmac.new(
        secret.encode("utf-8"),
        canonical_json_bytes(signed),
        hashlib.sha256,
    ).hexdigest()
    signed["signature"] = {
        "algorithm": SIGNATURE_ALGORITHM,
        "value": digest,
    }
    return signed


def verify_payload_signature(payload: Mapping[str, Any], secret: str) -> bool:
    signature = payload.get("signature")
    if not isinstance(signature, Mapping):
        return False
    if signature.get("algorithm") != SIGNATURE_ALGORITHM:
        return False
    supplied = str(signature.get("value", ""))
    expected = hmac.new(
        secret.encode("utf-8"),
        canonical_json_bytes(_unsigned_payload(payload)),
        hashlib.sha256,
    ).hexdigest()
    return bool(supplied) and hmac.compare_digest(supplied, expected)


def configuration_fingerprint(env: Mapping[str, str], secret: str) -> str:
    material = {key: str(env.get(key, "")) for key in CONFIG_FINGERPRINT_KEYS}
    return hmac.new(
        secret.encode("utf-8"),
        canonical_json_bytes(material),
        hashlib.sha256,
    ).hexdigest()


def release_binding(state: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "release_id": state.get("release_id"),
        "git_commit": state.get("git_commit"),
        "sha256": state.get("sha256"),
        "promoted_at": state.get("promoted_at"),
    }


def validate_release_binding(binding: Mapping[str, Any], current: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    for key in ("release_id", "git_commit", "sha256"):
        expected = str(current.get(key, ""))
        actual = str(binding.get(key, ""))
        if not expected:
            errors.append(f"current release is missing {key}")
        elif actual != expected:
            errors.append(f"release {key} mismatch")
    return errors


def load_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"Evidence file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"Evidence file is invalid JSON: {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"Evidence file must contain a JSON object: {path}")
    return payload


def atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)


def atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_evidence_window(
    payload: Mapping[str, Any],
    *,
    now: datetime | None,
    maximum_age: timedelta,
) -> list[str]:
    errors: list[str] = []
    current = (now or utc_now()).astimezone(UTC)
    try:
        created = parse_timestamp(payload.get("created_at"), "created_at")
        expires = parse_timestamp(payload.get("expires_at"), "expires_at")
    except ValueError as exc:
        return [str(exc)]
    if created > current + timedelta(minutes=5):
        errors.append("evidence created_at is too far in the future")
    if expires <= created:
        errors.append("evidence expires_at must be after created_at")
    if current > expires:
        errors.append("evidence has expired")
    if current - created > maximum_age:
        errors.append("evidence is older than the allowed maximum age")
    return errors


def validate_provider_report(
    report: Mapping[str, Any],
    *,
    env: Mapping[str, str],
    current_release: Mapping[str, Any],
    now: datetime | None = None,
    max_age_minutes: int = 60,
) -> list[str]:
    errors: list[str] = []
    try:
        secret = require_signing_secret(env)
    except ValueError as exc:
        return [str(exc)]

    if report.get("schema_version") != SCHEMA_VERSION:
        errors.append("unsupported provider evidence schema")
    if report.get("kind") != "provider_probes":
        errors.append("evidence kind must be provider_probes")
    if report.get("mode") != "strict":
        errors.append("provider evidence must be strict")
    if report.get("go") is not True:
        errors.append("provider evidence decision is not GO")
    if not verify_payload_signature(report, secret):
        errors.append("provider evidence signature is invalid")
    if report.get("configuration_fingerprint") != configuration_fingerprint(env, secret):
        errors.append("provider evidence configuration fingerprint does not match")

    binding = report.get("release")
    if not isinstance(binding, Mapping):
        errors.append("provider evidence release binding is missing")
    else:
        errors.extend(validate_release_binding(binding, current_release))

    errors.extend(
        validate_evidence_window(
            report,
            now=now,
            maximum_age=timedelta(minutes=max_age_minutes),
        )
    )

    results = report.get("results")
    if not isinstance(results, list):
        errors.append("provider evidence results must be a list")
        results = []
    names = [str(item.get("name", "")) for item in results if isinstance(item, Mapping)]
    if set(names) != EXPECTED_PROVIDER_PROBES or len(names) != len(EXPECTED_PROVIDER_PROBES):
        errors.append("provider evidence must contain each required probe exactly once")
    if any(not isinstance(item, Mapping) or item.get("ok") is not True for item in results):
        errors.append("one or more provider probes did not pass")

    summary = report.get("summary")
    if not isinstance(summary, Mapping):
        errors.append("provider evidence summary is missing")
    else:
        expected_count = len(EXPECTED_PROVIDER_PROBES)
        if summary.get("total") != expected_count:
            errors.append("provider evidence total count is incorrect")
        if summary.get("passed") != expected_count or summary.get("failed") != 0:
            errors.append("provider evidence summary is not fully passing")
    return list(dict.fromkeys(errors))


def build_rollback_drill_report(
    *,
    from_release: Mapping[str, Any],
    to_release: Mapping[str, Any],
    backup_path: Path,
    env: Mapping[str, str],
    completed_at: datetime | None = None,
    max_age_days: int = 30,
) -> dict[str, Any]:
    secret = require_signing_secret(env)
    completed = (completed_at or utc_now()).astimezone(UTC)
    if not backup_path.is_file():
        raise ValueError(f"Rollback drill backup not found: {backup_path}")
    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "kind": "rollback_drill",
        "created_at": utc_timestamp(completed),
        "expires_at": utc_timestamp(completed + timedelta(days=max_age_days)),
        "result": "GO",
        "configuration_fingerprint": configuration_fingerprint(env, secret),
        "from_release": release_binding(from_release),
        "to_release": release_binding(to_release),
        "backup": {
            "path": str(backup_path.resolve()),
            "name": backup_path.name,
            "sha256": sha256_file(backup_path),
            "bytes": backup_path.stat().st_size,
        },
        "checks": {
            "database_restored": True,
            "migration_compatibility": True,
            "services_running": True,
            "container_smoke": True,
        },
    }
    return sign_payload(payload, secret)


def validate_rollback_drill_report(
    report: Mapping[str, Any],
    *,
    env: Mapping[str, str],
    now: datetime | None = None,
    max_age_days: int = 30,
    require_backup_file: bool = True,
) -> list[str]:
    errors: list[str] = []
    try:
        secret = require_signing_secret(env)
    except ValueError as exc:
        return [str(exc)]
    if report.get("schema_version") != SCHEMA_VERSION:
        errors.append("unsupported rollback evidence schema")
    if report.get("kind") != "rollback_drill":
        errors.append("evidence kind must be rollback_drill")
    if report.get("result") != "GO":
        errors.append("rollback drill result is not GO")
    if not verify_payload_signature(report, secret):
        errors.append("rollback drill signature is invalid")
    if report.get("configuration_fingerprint") != configuration_fingerprint(env, secret):
        errors.append("rollback drill configuration fingerprint does not match")
    errors.extend(
        validate_evidence_window(
            report,
            now=now,
            maximum_age=timedelta(days=max_age_days),
        )
    )

    from_release = report.get("from_release")
    to_release = report.get("to_release")
    if not isinstance(from_release, Mapping) or not isinstance(to_release, Mapping):
        errors.append("rollback drill release bindings are missing")
    elif from_release.get("sha256") == to_release.get("sha256"):
        errors.append("rollback drill must switch between different releases")

    checks = report.get("checks")
    if not isinstance(checks, Mapping) or any(value is not True for value in checks.values()):
        errors.append("rollback drill checks are not fully passing")

    backup = report.get("backup")
    if not isinstance(backup, Mapping):
        errors.append("rollback drill backup evidence is missing")
    elif require_backup_file:
        path = Path(str(backup.get("path", "")))
        if not path.is_file():
            errors.append("rollback drill backup file is no longer available")
        else:
            if backup.get("sha256") != sha256_file(path):
                errors.append("rollback drill backup checksum does not match")
            if backup.get("bytes") != path.stat().st_size:
                errors.append("rollback drill backup size does not match")
    return list(dict.fromkeys(errors))


DEFAULT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ROLLBACK_REPORT = DEFAULT_ROOT / "docs/pilot/rollback_drill_report.json"


def read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def render_rollback_markdown(report: Mapping[str, Any]) -> str:
    source = report.get("from_release") if isinstance(report.get("from_release"), Mapping) else {}
    target = report.get("to_release") if isinstance(report.get("to_release"), Mapping) else {}
    backup = report.get("backup") if isinstance(report.get("backup"), Mapping) else {}
    return "\n".join(
        [
            "# FLASHIN rollback drill evidence",
            "",
            f"Decision: **{report.get('result', 'NO-GO')}**",
            "",
            f"Completed: `{report.get('created_at')}`",
            f"Expires: `{report.get('expires_at')}`",
            f"From release: `{source.get('release_id', 'unknown')}`",
            f"To release: `{target.get('release_id', 'unknown')}`",
            f"Backup: `{backup.get('name', 'unknown')}`",
            f"Backup SHA-256: `{backup.get('sha256', 'unknown')}`",
            "",
        ]
    )


def _cli() -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    secret = sub.add_parser("validate-secret")
    secret.add_argument("--env", type=Path, default=DEFAULT_ROOT / ".env")

    record = sub.add_parser("record-rollback")
    record.add_argument("--env", type=Path, default=DEFAULT_ROOT / ".env")
    record.add_argument("--from-release-state", type=Path, required=True)
    record.add_argument("--to-release-state", type=Path, required=True)
    record.add_argument("--backup", type=Path, required=True)
    record.add_argument("--report", type=Path, default=DEFAULT_ROLLBACK_REPORT)
    record.add_argument("--max-age-days", type=int)

    verify = sub.add_parser("verify-rollback")
    verify.add_argument("--env", type=Path, default=DEFAULT_ROOT / ".env")
    verify.add_argument("--report", type=Path, default=DEFAULT_ROLLBACK_REPORT)
    verify.add_argument("--max-age-days", type=int)

    args = parser.parse_args()
    env = read_env_file(args.env)
    try:
        configured_days = str(env.get("PILOT_ROLLBACK_DRILL_MAX_AGE_DAYS", "30")).strip()
        try:
            max_age_days = int(getattr(args, "max_age_days", None) or configured_days)
        except ValueError as exc:
            raise ValueError("PILOT_ROLLBACK_DRILL_MAX_AGE_DAYS must be an integer") from exc
        if args.command == "validate-secret":
            require_signing_secret(env)
            print(json.dumps({"ok": True}))
            return 0
        if args.command == "record-rollback":
            if max_age_days < 1 or max_age_days > 90:
                raise ValueError("max age days must be between 1 and 90")
            report = build_rollback_drill_report(
                from_release=load_json(args.from_release_state),
                to_release=load_json(args.to_release_state),
                backup_path=args.backup.resolve(),
                env=env,
                max_age_days=max_age_days,
            )
            atomic_write_json(args.report, report)
            atomic_write_text(args.report.with_suffix(".md"), render_rollback_markdown(report))
            print(json.dumps({"ok": True, "report": str(args.report)}, ensure_ascii=False))
            return 0
        if args.command == "verify-rollback":
            report = load_json(args.report)
            errors = validate_rollback_drill_report(
                report,
                env=env,
                max_age_days=max_age_days,
            )
            print(json.dumps({"ok": not errors, "errors": errors}, ensure_ascii=False))
            return 0 if not errors else 1
    except (OSError, ValueError) as exc:
        print(json.dumps({"ok": False, "errors": [str(exc)]}, ensure_ascii=False))
        return 1
    return 1


if __name__ == "__main__":
    raise SystemExit(_cli())
