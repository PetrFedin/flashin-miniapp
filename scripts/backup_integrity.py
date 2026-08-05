#!/usr/bin/env python3
"""Create and verify signed, restore-proven PostgreSQL backup manifests.

The manifest is derived from a temporary database restored from the compressed
archive, never from the live database after pg_dump. This binds the signature to
the exact bytes that can actually be restored and avoids a race with concurrent
writes. The module intentionally uses only the Python standard library plus the
existing Docker Compose PostgreSQL service.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import subprocess
import sys
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

from pilot_evidence import (  # noqa: E402
    atomic_write_json,
    load_json,
    require_signing_secret,
    sha256_file,
    sign_payload,
    utc_timestamp,
    verify_payload_signature,
)
from pilot_readiness import read_env  # noqa: E402

SCHEMA_VERSION = 1
KIND = "postgres_backup_manifest"
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
CRITICAL_TABLES = (
    "customers",
    "products",
    "product_variants",
    "carts",
    "cart_items",
    "orders",
    "order_items",
    "payments",
    "payment_events",
    "return_requests",
    "refund_attempts",
    "inventory_reservations",
    "loyalty_transactions",
    "referral_codes",
    "referral_attributions",
    "pilot_runtime_state",
    "pilot_order_slots",
)


class BackupIntegrityError(ValueError):
    """A fail-closed backup or restore integrity error."""


def _validate_identifier(value: str, field: str) -> str:
    normalized = str(value or "").strip()
    if not _IDENTIFIER.fullmatch(normalized):
        raise BackupIntegrityError(f"{field} must be a valid PostgreSQL identifier")
    if normalized in {"template0", "template1"}:
        raise BackupIntegrityError(f"{field} cannot use a reserved template database")
    return normalized


def _run(
    command: list[str],
    *,
    input_text: str | None = None,
    capture: bool = True,
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            command,
            input=input_text,
            text=True,
            check=True,
            capture_output=capture,
        )
    except FileNotFoundError as exc:
        raise BackupIntegrityError(f"Required command is unavailable: {command[0]}") from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "").strip()
        raise BackupIntegrityError(
            f"Command failed ({' '.join(command[:4])}): {detail or exc.returncode}"
        ) from exc


def _compose_env(name: str) -> str:
    result = _run(
        [
            "docker",
            "compose",
            "exec",
            "-T",
            "db",
            "sh",
            "-ec",
            'printf %s "${!1}"',
            "sh",
            name,
        ]
    )
    value = result.stdout.strip()
    if not value:
        raise BackupIntegrityError(f"PostgreSQL container variable is missing: {name}")
    return value


def _psql(database: str, sql: str) -> str:
    db_name = _validate_identifier(database, "database")
    result = _run(
        [
            "docker",
            "compose",
            "exec",
            "-T",
            "db",
            "sh",
            "-ec",
            'exec psql --set ON_ERROR_STOP=on -U "$POSTGRES_USER" -d "$1" -tAX -c "$2"',
            "sh",
            db_name,
            sql,
        ]
    )
    return result.stdout.strip()


def _psql_lines(database: str, sql: str) -> list[str]:
    output = _psql(database, sql)
    return [line.strip() for line in output.splitlines() if line.strip()]


def _drop_database(database: str) -> None:
    db_name = _validate_identifier(database, "temporary database")
    sql = (
        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
        f"WHERE datname = '{db_name}' AND pid <> pg_backend_pid(); "
        f'DROP DATABASE IF EXISTS "{db_name}";'
    )
    _psql("postgres", sql)


def _create_database(database: str) -> None:
    db_name = _validate_identifier(database, "temporary database")
    owner = _validate_identifier(_compose_env("POSTGRES_USER"), "POSTGRES_USER")
    _drop_database(db_name)
    _psql("postgres", f'CREATE DATABASE "{db_name}" OWNER "{owner}";')


def _restore_archive(backup: Path, database: str) -> None:
    db_name = _validate_identifier(database, "restore database")
    gzip_process = subprocess.Popen(
        ["gzip", "-dc", str(backup)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert gzip_process.stdout is not None
    try:
        psql_process = subprocess.Popen(
            [
                "docker",
                "compose",
                "exec",
                "-T",
                "db",
                "sh",
                "-ec",
                'exec psql --set ON_ERROR_STOP=on -U "$POSTGRES_USER" -d "$1"',
                "sh",
                db_name,
            ],
            stdin=gzip_process.stdout,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except FileNotFoundError as exc:
        gzip_process.kill()
        raise BackupIntegrityError("docker is required to restore PostgreSQL backups") from exc
    gzip_process.stdout.close()
    psql_stdout, psql_stderr = psql_process.communicate()
    gzip_stderr = gzip_process.communicate()[1]
    if gzip_process.returncode != 0:
        raise BackupIntegrityError(
            f"Backup gzip stream is invalid: {gzip_stderr.decode('utf-8', 'replace').strip()}"
        )
    if psql_process.returncode != 0:
        detail = psql_stderr.decode("utf-8", "replace").strip()
        raise BackupIntegrityError(f"PostgreSQL restore failed: {detail}")
    del psql_stdout


def _sha256_lines(lines: list[str]) -> str:
    digest = hashlib.sha256()
    for line in lines:
        digest.update(line.encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def snapshot_database(database: str) -> dict[str, Any]:
    db_name = _validate_identifier(database, "snapshot database")
    tables = _psql_lines(
        db_name,
        "SELECT tablename FROM pg_catalog.pg_tables "
        "WHERE schemaname = 'public' ORDER BY tablename;",
    )
    if not tables:
        raise BackupIntegrityError("Restored database contains no public tables")
    if "alembic_version" not in tables:
        raise BackupIntegrityError("Restored database is missing alembic_version")

    revisions = _psql_lines(
        db_name,
        "SELECT version_num FROM public.alembic_version ORDER BY version_num;",
    )
    if len(revisions) != 1:
        raise BackupIntegrityError(
            f"Restored database must contain exactly one Alembic revision, found {len(revisions)}"
        )

    schema_lines = _psql_lines(
        db_name,
        "SELECT table_name || '|' || ordinal_position::text || '|' || column_name || '|' || "
        "data_type || '|' || is_nullable || '|' || COALESCE(column_default, '') "
        "FROM information_schema.columns WHERE table_schema = 'public' "
        "ORDER BY table_name, ordinal_position;",
    )
    if not schema_lines:
        raise BackupIntegrityError("Restored database schema metadata is empty")

    table_set = set(tables)
    critical: dict[str, dict[str, Any]] = {}
    for table in CRITICAL_TABLES:
        if table not in table_set:
            continue
        quoted = f'"{table}"'
        count_text = _psql(db_name, f"SELECT COUNT(*) FROM public.{quoted};")
        try:
            row_count = int(count_text)
        except ValueError as exc:
            raise BackupIntegrityError(f"Invalid row count returned for {table}") from exc
        row_hashes = _psql_lines(
            db_name,
            f"SELECT md5(row_to_json(t)::text) FROM public.{quoted} AS t ORDER BY 1;",
        )
        if len(row_hashes) != row_count:
            raise BackupIntegrityError(
                f"Critical table changed while it was fingerprinted: {table}"
            )
        critical[table] = {
            "rows": row_count,
            "content_sha256": _sha256_lines(row_hashes),
        }

    return {
        "alembic_revision": revisions[0],
        "public_table_count": len(tables),
        "public_tables_sha256": _sha256_lines(tables),
        "schema_sha256": _sha256_lines(schema_lines),
        "critical_tables": critical,
    }


def build_manifest(
    backup: Path,
    snapshot: Mapping[str, Any],
    *,
    source_database: str,
    secret: str,
    created_at: str | None = None,
) -> dict[str, Any]:
    if not backup.is_file():
        raise BackupIntegrityError(f"Backup file not found: {backup}")
    if backup.stat().st_size <= 0:
        raise BackupIntegrityError("Backup file is empty")
    payload = {
        "schema_version": SCHEMA_VERSION,
        "kind": KIND,
        "created_at": created_at or utc_timestamp(),
        "source_database": _validate_identifier(source_database, "source database"),
        "backup": {
            "sha256": sha256_file(backup),
            "size_bytes": backup.stat().st_size,
        },
        "database_snapshot": dict(snapshot),
    }
    return sign_payload(payload, secret)


def validate_manifest(
    manifest: Mapping[str, Any],
    backup: Path,
    secret: str,
) -> list[str]:
    errors: list[str] = []
    if manifest.get("schema_version") != SCHEMA_VERSION:
        errors.append("unsupported backup manifest schema")
    if manifest.get("kind") != KIND:
        errors.append("backup manifest kind is invalid")
    if not verify_payload_signature(manifest, secret):
        errors.append("backup manifest signature is invalid")

    backup_meta = manifest.get("backup")
    if not isinstance(backup_meta, Mapping):
        errors.append("backup manifest metadata is missing")
    elif not backup.is_file():
        errors.append(f"backup file not found: {backup}")
    else:
        if backup_meta.get("sha256") != sha256_file(backup):
            errors.append("backup SHA-256 does not match signed manifest")
        if backup_meta.get("size_bytes") != backup.stat().st_size:
            errors.append("backup size does not match signed manifest")

    snapshot = manifest.get("database_snapshot")
    if not isinstance(snapshot, Mapping):
        errors.append("backup database snapshot is missing")
    else:
        if not str(snapshot.get("alembic_revision") or "").strip():
            errors.append("backup Alembic revision is missing")
        if not str(snapshot.get("schema_sha256") or "").strip():
            errors.append("backup schema digest is missing")
        if not isinstance(snapshot.get("critical_tables"), Mapping):
            errors.append("backup critical table fingerprints are missing")
    return list(dict.fromkeys(errors))


def compare_snapshots(
    expected: Mapping[str, Any],
    actual: Mapping[str, Any],
) -> list[str]:
    errors: list[str] = []
    for key in (
        "alembic_revision",
        "public_table_count",
        "public_tables_sha256",
        "schema_sha256",
    ):
        if actual.get(key) != expected.get(key):
            errors.append(f"restored database {key} does not match backup manifest")

    expected_tables = expected.get("critical_tables")
    actual_tables = actual.get("critical_tables")
    if not isinstance(expected_tables, Mapping) or not isinstance(actual_tables, Mapping):
        errors.append("restored critical table fingerprints are invalid")
        return errors
    if set(actual_tables) != set(expected_tables):
        errors.append("restored critical table set does not match backup manifest")
    for table in sorted(set(expected_tables) & set(actual_tables)):
        expected_table = expected_tables.get(table)
        actual_table = actual_tables.get(table)
        if not isinstance(expected_table, Mapping) or not isinstance(actual_table, Mapping):
            errors.append(f"restored critical table metadata is invalid: {table}")
            continue
        for field in ("rows", "content_sha256"):
            if actual_table.get(field) != expected_table.get(field):
                errors.append(f"restored critical table {table} {field} does not match")
    return list(dict.fromkeys(errors))


def _manifest_path(backup: Path, supplied: Path | None) -> Path:
    return supplied or Path(f"{backup}.manifest.json")


def _secret(env_path: Path) -> str:
    env = read_env(env_path)
    environment_secret = os.environ.get("PILOT_EVIDENCE_SIGNING_SECRET", "").strip()
    if environment_secret:
        env["PILOT_EVIDENCE_SIGNING_SECRET"] = environment_secret
    return require_signing_secret(env)


def _temporary_database(prefix: str) -> str:
    safe_prefix = re.sub(r"[^A-Za-z0-9_]", "_", prefix)[:30] or "flashin_backup"
    return _validate_identifier(
        f"{safe_prefix}_{os.getpid()}_{secrets.token_hex(4)}",
        "temporary database",
    )


def _restore_snapshot(backup: Path, database: str) -> dict[str, Any]:
    _create_database(database)
    try:
        _restore_archive(backup, database)
        return snapshot_database(database)
    finally:
        _drop_database(database)


def create_manifest(
    backup: Path,
    manifest_path: Path,
    env_path: Path,
) -> dict[str, Any]:
    secret = _secret(env_path)
    source_database = _validate_identifier(_compose_env("POSTGRES_DB"), "POSTGRES_DB")
    snapshot = _restore_snapshot(backup, _temporary_database("flashin_backup_create"))
    manifest = build_manifest(
        backup,
        snapshot,
        source_database=source_database,
        secret=secret,
    )
    atomic_write_json(manifest_path, manifest)
    return manifest


def verify_archive(
    backup: Path,
    manifest_path: Path,
    env_path: Path,
) -> dict[str, Any]:
    secret = _secret(env_path)
    manifest = load_json(manifest_path)
    errors = validate_manifest(manifest, backup, secret)
    if errors:
        raise BackupIntegrityError("; ".join(errors))
    return manifest


def verify_restorable(
    backup: Path,
    manifest_path: Path,
    env_path: Path,
) -> dict[str, Any]:
    manifest = verify_archive(backup, manifest_path, env_path)
    actual = _restore_snapshot(backup, _temporary_database("flashin_backup_verify"))
    expected = manifest.get("database_snapshot")
    assert isinstance(expected, Mapping)
    errors = compare_snapshots(expected, actual)
    if errors:
        raise BackupIntegrityError("; ".join(errors))
    return manifest


def verify_live_database(
    backup: Path,
    manifest_path: Path,
    env_path: Path,
    database: str,
) -> dict[str, Any]:
    manifest = verify_archive(backup, manifest_path, env_path)
    actual = snapshot_database(database)
    expected = manifest.get("database_snapshot")
    assert isinstance(expected, Mapping)
    errors = compare_snapshots(expected, actual)
    if errors:
        raise BackupIntegrityError("; ".join(errors))
    return manifest


def parser() -> argparse.ArgumentParser:
    command = argparse.ArgumentParser(description=__doc__)
    sub = command.add_subparsers(dest="command", required=True)
    for name in ("create", "verify-archive", "verify", "verify-live"):
        item = sub.add_parser(name)
        item.add_argument("--backup", type=Path, required=True)
        item.add_argument("--manifest", type=Path)
        item.add_argument("--env", type=Path, default=ROOT / ".env")
        if name == "verify-live":
            item.add_argument("--database", required=True)
    return command


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    backup = args.backup.resolve()
    manifest_path = _manifest_path(backup, args.manifest)
    if not manifest_path.is_absolute():
        manifest_path = manifest_path.resolve()
    try:
        if args.command == "create":
            manifest = create_manifest(backup, manifest_path, args.env)
        elif args.command == "verify-archive":
            manifest = verify_archive(backup, manifest_path, args.env)
        elif args.command == "verify":
            manifest = verify_restorable(backup, manifest_path, args.env)
        else:
            database = _validate_identifier(args.database, "database")
            manifest = verify_live_database(
                backup,
                manifest_path,
                args.env,
                database,
            )
        snapshot = manifest.get("database_snapshot") or {}
        print(
            json.dumps(
                {
                    "ok": True,
                    "command": args.command,
                    "backup": str(backup),
                    "manifest": str(manifest_path),
                    "sha256": (manifest.get("backup") or {}).get("sha256"),
                    "alembic_revision": snapshot.get("alembic_revision"),
                    "public_table_count": snapshot.get("public_table_count"),
                    "critical_tables": len(snapshot.get("critical_tables") or {}),
                },
                ensure_ascii=False,
            )
        )
        return 0
    except (BackupIntegrityError, OSError, ValueError) as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "command": args.command,
                    "backup": str(backup),
                    "manifest": str(manifest_path),
                    "errors": [str(exc)],
                },
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
