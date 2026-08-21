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
BACKUP_MANIFEST_KIND = "postgres_backup_manifest"
RECOVERY_SCOPES = frozenset({"ci", "pilot_host"})
RECOVERY_ENV_KEYS = (
    "PILOT_RECOVERY_SCOPE",
    "PILOT_RECOVERY_HOST_ID",
    "PILOT_RECOVERY_RTO_SECONDS",
    "PILOT_RECOVERY_RPO_SECONDS",
    "PILOT_RECOVERY_STARTED_AT",
    "PILOT_RECOVERY_BACKUP_MANIFEST",
)
EXPECTED_PROVIDER_PROBES = {
    "telegram",
    "yookassa",
    "moysklad",
    "r2_s3",
    "meilisearch",
}
PROVIDER_RESULT_KEYS = frozenset({"name", "ok", "returncode", "stdout", "stderr"})
CONFIG_FINGERPRINT_KEYS = (
    "APP_ENV",
    "API_PUBLIC_URL",
    "MINI_APP_URL",
    "ADMIN_URL",
    "TELEGRAM_BOT_TOKEN",
    "YOOKASSA_SHOP_ID",
    "YOOKASSA_SECRET_KEY",
    "YOOKASSA_RETURN_URL",
    "YOOKASSA_WEBHOOK_URL",
    "MOYSKLAD_BASE_URL",
    "MOYSKLAD_TOKEN",
    "MOYSKLAD_LOGIN",
    "MOYSKLAD_PASSWORD",
    "MOYSKLAD_SALE_PRICE_TYPE",
    "MOYSKLAD_SIZE_ATTRIBUTE_NAMES",
    "MOYSKLAD_COLOR_ATTRIBUTE_NAMES",
    "MOYSKLAD_ORDER_EXPORT_ENABLED",
    "MOYSKLAD_ORGANIZATION_ID",
    "MOYSKLAD_AGENT_ID",
    "MOYSKLAD_STORE_ID",
    "MOYSKLAD_DELIVERY_SERVICE_ID",
    "MOYSKLAD_SYNC_INTERVAL_MINUTES",
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
    "SCHEDULER_ENABLED",
    "PROVIDER_COMMAND_POLL_SECONDS",
    "NOTIFICATION_MAX_ATTEMPTS",
    "NOTIFICATION_LEASE_SECONDS",
    "RATE_LIMIT_ENABLED",
    "RATE_LIMIT_PER_MINUTE",
    "PILOT_RUNTIME_ENFORCED",
    "PILOT_RUNTIME_MAX_ORDERS",
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


def validate_provider_result_records(results: Any) -> list[str]:
    """Validate the status-only persisted shape shared by verify and admission."""
    if not isinstance(results, list):
        return ["provider evidence results must be a list"]

    errors: list[str] = []
    for item in results:
        if not isinstance(item, Mapping):
            errors.append("provider evidence result must be an object")
            continue
        fields = set(item)
        if fields - PROVIDER_RESULT_KEYS:
            errors.append("provider evidence result contains unsupported fields")
        if PROVIDER_RESULT_KEYS - fields:
            errors.append("provider evidence result fields are incomplete")
        if str(item.get("stdout", "")).strip() or str(item.get("stderr", "")).strip():
            errors.append("provider evidence must not retain probe stdout/stderr")
        if not isinstance(item.get("ok"), bool):
            errors.append("provider evidence result ok must be boolean")
        returncode = item.get("returncode")
        if returncode is not None and (
            not isinstance(returncode, int) or isinstance(returncode, bool)
        ):
            errors.append("provider evidence returncode must be an integer or null")
        if item.get("ok") is True and returncode != 0:
            errors.append("passing provider evidence result must have returncode 0")
    return list(dict.fromkeys(errors))


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
    errors.extend(validate_provider_result_records(results))
    if not isinstance(results, list):
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


def _positive_seconds(value: Any, field: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be a positive integer")
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be a positive integer") from exc
    if parsed <= 0:
        raise ValueError(f"{field} must be a positive integer")
    return parsed


def recovery_host_fingerprint(host_id: str, secret: str) -> str:
    normalized = str(host_id).strip()
    if not normalized:
        raise ValueError("PILOT_RECOVERY_HOST_ID is required for pilot_host recovery evidence")
    return hmac.new(
        secret.encode("utf-8"),
        f"flashin-recovery-host:{normalized}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _verified_backup_manifest(
    manifest_path: Path,
    backup_path: Path,
    secret: str,
) -> tuple[dict[str, Any], datetime]:
    manifest = load_json(manifest_path)
    errors: list[str] = []
    if manifest.get("schema_version") != SCHEMA_VERSION:
        errors.append("unsupported backup manifest schema")
    if manifest.get("kind") != BACKUP_MANIFEST_KIND:
        errors.append("backup manifest kind is invalid")
    if not verify_payload_signature(manifest, secret):
        errors.append("backup manifest signature is invalid")
    backup_meta = manifest.get("backup")
    if not isinstance(backup_meta, Mapping):
        errors.append("backup manifest metadata is missing")
    elif not backup_path.is_file():
        errors.append(f"backup file not found: {backup_path}")
    else:
        if backup_meta.get("sha256") != sha256_file(backup_path):
            errors.append("backup SHA-256 does not match signed manifest")
        if backup_meta.get("size_bytes") != backup_path.stat().st_size:
            errors.append("backup size does not match signed manifest")
    try:
        created = parse_timestamp(manifest.get("created_at"), "backup manifest created_at")
    except ValueError as exc:
        errors.append(str(exc))
        created = datetime.min.replace(tzinfo=UTC)
    if errors:
        raise ValueError("; ".join(dict.fromkeys(errors)))
    return manifest, created


def build_rollback_drill_report(
    *,
    from_release: Mapping[str, Any],
    to_release: Mapping[str, Any],
    backup_path: Path,
    env: Mapping[str, str],
    completed_at: datetime | None = None,
    max_age_days: int = 30,
    recovery_scope: str = "ci",
    recovery_host_id: str | None = None,
    rto_target_seconds: int | str | None = None,
    rpo_target_seconds: int | str | None = None,
    started_at: datetime | None = None,
    backup_manifest_path: Path | None = None,
) -> dict[str, Any]:
    secret = require_signing_secret(env)
    completed = parse_timestamp(utc_timestamp(completed_at or utc_now()), "completed_at")
    started = parse_timestamp(utc_timestamp(started_at or completed), "started_at")
    if started > completed:
        raise ValueError("recovery started_at cannot be after completed_at")
    if not backup_path.is_file():
        raise ValueError(f"Rollback drill backup not found: {backup_path}")

    scope = str(recovery_scope or "ci").strip().lower()
    if scope not in RECOVERY_SCOPES:
        raise ValueError("recovery scope must be ci or pilot_host")
    duration_seconds = int((completed - started).total_seconds())
    recovery: dict[str, Any] = {
        "scope": scope,
        "started_at": utc_timestamp(started),
        "completed_at": utc_timestamp(completed),
        "duration_seconds": duration_seconds,
        "host_fingerprint": None,
        "rto_target_seconds": None,
        "rpo_target_seconds": None,
        "backup_age_seconds": None,
        "rto_met": None,
        "rpo_met": None,
        "backup_manifest": None,
    }
    result = "GO"

    if scope == "pilot_host":
        rto_target = _positive_seconds(rto_target_seconds, "PILOT_RECOVERY_RTO_SECONDS")
        rpo_target = _positive_seconds(rpo_target_seconds, "PILOT_RECOVERY_RPO_SECONDS")
        manifest_path = (backup_manifest_path or Path(f"{backup_path}.manifest.json")).resolve()
        manifest, backup_created = _verified_backup_manifest(manifest_path, backup_path, secret)
        if backup_created > started:
            raise ValueError("backup manifest created_at cannot be after recovery started_at")
        backup_age_seconds = int((started - backup_created).total_seconds())
        rto_met = duration_seconds <= rto_target
        rpo_met = backup_age_seconds <= rpo_target
        recovery.update(
            {
                "host_fingerprint": recovery_host_fingerprint(recovery_host_id or "", secret),
                "rto_target_seconds": rto_target,
                "rpo_target_seconds": rpo_target,
                "backup_age_seconds": backup_age_seconds,
                "rto_met": rto_met,
                "rpo_met": rpo_met,
                "backup_manifest": {
                    "path": str(manifest_path),
                    "sha256": sha256_file(manifest_path),
                    "created_at": manifest.get("created_at"),
                },
            }
        )
        if not rto_met or not rpo_met:
            result = "NO-GO"
    elif any(
        value not in (None, "")
        for value in (recovery_host_id, rto_target_seconds, rpo_target_seconds, backup_manifest_path)
    ):
        raise ValueError("CI recovery evidence must not carry pilot-host identity or RTO/RPO targets")

    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "kind": "rollback_drill",
        "created_at": utc_timestamp(completed),
        "expires_at": utc_timestamp(completed + timedelta(days=max_age_days)),
        "result": result,
        "configuration_fingerprint": configuration_fingerprint(env, secret),
        "from_release": release_binding(from_release),
        "to_release": release_binding(to_release),
        "backup": {
            "path": str(backup_path.resolve()),
            "name": backup_path.name,
            "sha256": sha256_file(backup_path),
            "bytes": backup_path.stat().st_size,
        },
        "recovery": recovery,
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
    required_scope: str | None = None,
    expected_host_id: str | None = None,
    expected_rto_target_seconds: int | str | None = None,
    expected_rpo_target_seconds: int | str | None = None,
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
    backup_path: Path | None = None
    if not isinstance(backup, Mapping):
        errors.append("rollback drill backup evidence is missing")
    else:
        backup_path = Path(str(backup.get("path", "")))
        if require_backup_file:
            if not backup_path.is_file():
                errors.append("rollback drill backup file is no longer available")
            else:
                if backup.get("sha256") != sha256_file(backup_path):
                    errors.append("rollback drill backup checksum does not match")
                if backup.get("bytes") != backup_path.stat().st_size:
                    errors.append("rollback drill backup size does not match")

    required = str(required_scope).strip().lower() if required_scope else None
    if required and required not in RECOVERY_SCOPES:
        errors.append("required recovery scope must be ci or pilot_host")

    recovery = report.get("recovery")
    if not isinstance(recovery, Mapping):
        if required:
            errors.append(f"rollback drill is missing required {required} recovery evidence")
        return list(dict.fromkeys(errors))

    scope = str(recovery.get("scope", "")).strip().lower()
    if scope not in RECOVERY_SCOPES:
        errors.append("rollback drill recovery scope is invalid")
    if required and scope != required:
        errors.append(f"rollback drill recovery scope must be {required}")

    try:
        started = parse_timestamp(recovery.get("started_at"), "recovery started_at")
        completed = parse_timestamp(recovery.get("completed_at"), "recovery completed_at")
        report_completed = parse_timestamp(report.get("created_at"), "created_at")
        if completed != report_completed:
            errors.append("recovery completed_at does not match rollback evidence created_at")
        if started > completed:
            errors.append("recovery started_at is after completed_at")
        expected_duration = int((completed - started).total_seconds())
        if recovery.get("duration_seconds") != expected_duration:
            errors.append("recovery duration does not match signed timestamps")
    except ValueError as exc:
        errors.append(str(exc))
        started = None
        expected_duration = None

    if scope == "ci":
        if any(
            recovery.get(key) not in (None, "")
            for key in (
                "host_fingerprint",
                "rto_target_seconds",
                "rpo_target_seconds",
                "backup_age_seconds",
                "rto_met",
                "rpo_met",
                "backup_manifest",
            )
        ):
            errors.append("CI recovery evidence contains pilot-host-only fields")
    elif scope == "pilot_host":
        try:
            rto_target = _positive_seconds(
                recovery.get("rto_target_seconds"), "recovery rto_target_seconds"
            )
            rpo_target = _positive_seconds(
                recovery.get("rpo_target_seconds"), "recovery rpo_target_seconds"
            )
        except ValueError as exc:
            errors.append(str(exc))
            rto_target = None
            rpo_target = None

        host_fingerprint = str(recovery.get("host_fingerprint", "")).strip()
        if not host_fingerprint:
            errors.append("pilot-host recovery fingerprint is missing")
        if required == "pilot_host":
            if not str(expected_host_id or "").strip():
                errors.append("expected pilot recovery host id is missing")
            else:
                try:
                    expected_fingerprint = recovery_host_fingerprint(expected_host_id or "", secret)
                    if not hmac.compare_digest(host_fingerprint, expected_fingerprint):
                        errors.append("pilot-host recovery fingerprint does not match")
                except ValueError as exc:
                    errors.append(str(exc))
            try:
                expected_rto = _positive_seconds(
                    expected_rto_target_seconds, "expected PILOT_RECOVERY_RTO_SECONDS"
                )
                if rto_target is not None and rto_target != expected_rto:
                    errors.append("pilot recovery RTO target does not match configured target")
            except ValueError as exc:
                errors.append(str(exc))
            try:
                expected_rpo = _positive_seconds(
                    expected_rpo_target_seconds, "expected PILOT_RECOVERY_RPO_SECONDS"
                )
                if rpo_target is not None and rpo_target != expected_rpo:
                    errors.append("pilot recovery RPO target does not match configured target")
            except ValueError as exc:
                errors.append(str(exc))

        manifest_entry = recovery.get("backup_manifest")
        if not isinstance(manifest_entry, Mapping):
            errors.append("pilot-host recovery backup manifest binding is missing")
        elif backup_path is None:
            errors.append("pilot-host recovery cannot verify backup manifest without backup evidence")
        else:
            manifest_path = Path(str(manifest_entry.get("path", "")))
            if not manifest_path.is_file():
                errors.append("pilot-host recovery backup manifest file is unavailable")
            else:
                if manifest_entry.get("sha256") != sha256_file(manifest_path):
                    errors.append("pilot-host recovery backup manifest checksum does not match")
                try:
                    manifest, backup_created = _verified_backup_manifest(
                        manifest_path, backup_path, secret
                    )
                    if manifest_entry.get("created_at") != manifest.get("created_at"):
                        errors.append("pilot-host recovery backup manifest timestamp does not match")
                    if started is not None:
                        if backup_created > started:
                            errors.append("pilot-host recovery backup is newer than recovery start")
                        else:
                            expected_age = int((started - backup_created).total_seconds())
                            if recovery.get("backup_age_seconds") != expected_age:
                                errors.append("pilot recovery RPO age does not match signed backup time")
                            if rpo_target is not None:
                                computed_rpo_met = expected_age <= rpo_target
                                if recovery.get("rpo_met") is not computed_rpo_met:
                                    errors.append("pilot recovery RPO result does not match evidence")
                                if not computed_rpo_met:
                                    errors.append("pilot recovery RPO target was missed")
                except ValueError as exc:
                    errors.append(str(exc))

        if expected_duration is not None and rto_target is not None:
            computed_rto_met = expected_duration <= rto_target
            if recovery.get("rto_met") is not computed_rto_met:
                errors.append("pilot recovery RTO result does not match evidence")
            if not computed_rto_met:
                errors.append("pilot recovery RTO target was missed")

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
    recovery = report.get("recovery") if isinstance(report.get("recovery"), Mapping) else {}
    lines = [
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
        f"Recovery scope: `{recovery.get('scope', 'legacy')}`",
        f"Recovery duration: `{recovery.get('duration_seconds', 'unknown')}` seconds",
    ]
    if recovery.get("scope") == "pilot_host":
        lines.extend(
            [
                f"RTO target: `{recovery.get('rto_target_seconds')}` seconds / met: `{recovery.get('rto_met')}`",
                f"RPO target: `{recovery.get('rpo_target_seconds')}` seconds / met: `{recovery.get('rpo_met')}`",
                f"Backup age at recovery start: `{recovery.get('backup_age_seconds')}` seconds",
                f"Pilot host fingerprint: `{recovery.get('host_fingerprint')}`",
            ]
        )
    lines.append("")
    return "\n".join(lines)


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
    record.add_argument("--recovery-scope", choices=sorted(RECOVERY_SCOPES))
    record.add_argument("--recovery-host-id")
    record.add_argument("--rto-target-seconds")
    record.add_argument("--rpo-target-seconds")
    record.add_argument("--started-at")
    record.add_argument("--backup-manifest", type=Path)

    verify = sub.add_parser("verify-rollback")
    verify.add_argument("--env", type=Path, default=DEFAULT_ROOT / ".env")
    verify.add_argument("--report", type=Path, default=DEFAULT_ROLLBACK_REPORT)
    verify.add_argument("--max-age-days", type=int)
    verify.add_argument("--require-scope", choices=sorted(RECOVERY_SCOPES))
    verify.add_argument("--expected-host-id")
    verify.add_argument("--expected-rto-seconds")
    verify.add_argument("--expected-rpo-seconds")

    args = parser.parse_args()
    env = read_env_file(args.env)
    for key in RECOVERY_ENV_KEYS:
        environment_value = os.environ.get(key)
        if environment_value is not None and environment_value.strip():
            env[key] = environment_value.strip()
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
            scope = args.recovery_scope or env.get("PILOT_RECOVERY_SCOPE", "ci")
            started_raw = args.started_at or env.get("PILOT_RECOVERY_STARTED_AT", "")
            started = parse_timestamp(started_raw, "PILOT_RECOVERY_STARTED_AT") if started_raw else None
            manifest_value = args.backup_manifest or env.get("PILOT_RECOVERY_BACKUP_MANIFEST", "")
            manifest_path = Path(manifest_value) if manifest_value else None
            host_id = args.recovery_host_id or env.get("PILOT_RECOVERY_HOST_ID", "")
            rto_target = args.rto_target_seconds or env.get("PILOT_RECOVERY_RTO_SECONDS", "")
            rpo_target = args.rpo_target_seconds or env.get("PILOT_RECOVERY_RPO_SECONDS", "")
            report = build_rollback_drill_report(
                from_release=load_json(args.from_release_state),
                to_release=load_json(args.to_release_state),
                backup_path=args.backup.resolve(),
                env=env,
                max_age_days=max_age_days,
                recovery_scope=scope,
                recovery_host_id=host_id or None,
                rto_target_seconds=rto_target or None,
                rpo_target_seconds=rpo_target or None,
                started_at=started,
                backup_manifest_path=manifest_path,
            )
            atomic_write_json(args.report, report)
            atomic_write_text(args.report.with_suffix(".md"), render_rollback_markdown(report))
            strict_scope = "pilot_host" if str(scope).strip().lower() == "pilot_host" else None
            errors = validate_rollback_drill_report(
                report,
                env=env,
                max_age_days=max_age_days,
                required_scope=strict_scope,
                expected_host_id=host_id or None,
                expected_rto_target_seconds=rto_target or None,
                expected_rpo_target_seconds=rpo_target or None,
            )
            print(json.dumps({"ok": not errors, "report": str(args.report), "errors": errors}, ensure_ascii=False))
            return 0 if not errors else 1
        if args.command == "verify-rollback":
            report = load_json(args.report)
            required_scope = args.require_scope
            host_id = args.expected_host_id or env.get("PILOT_RECOVERY_HOST_ID", "")
            rto_target = args.expected_rto_seconds or env.get("PILOT_RECOVERY_RTO_SECONDS", "")
            rpo_target = args.expected_rpo_seconds or env.get("PILOT_RECOVERY_RPO_SECONDS", "")
            errors = validate_rollback_drill_report(
                report,
                env=env,
                max_age_days=max_age_days,
                required_scope=required_scope,
                expected_host_id=host_id or None,
                expected_rto_target_seconds=rto_target or None,
                expected_rpo_target_seconds=rpo_target or None,
            )
            print(json.dumps({"ok": not errors, "errors": errors}, ensure_ascii=False))
            return 0 if not errors else 1
    except (OSError, ValueError) as exc:
        print(json.dumps({"ok": False, "errors": [str(exc)]}, ensure_ascii=False))
        return 1
    return 1


if __name__ == "__main__":
    raise SystemExit(_cli())
