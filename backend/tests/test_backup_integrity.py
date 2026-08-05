import copy
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from backup_integrity import (  # noqa: E402
    BackupIntegrityError,
    _validate_identifier,
    build_manifest,
    compare_snapshots,
    validate_manifest,
)


SECRET = "backup-integrity-test-secret-0123456789abcdef"


def snapshot():
    return {
        "alembic_revision": "0022_pilot_runtime_guard",
        "public_table_count": 54,
        "public_tables_sha256": "a" * 64,
        "schema_sha256": "b" * 64,
        "critical_tables": {
            "customers": {"rows": 2, "content_sha256": "c" * 64},
            "orders": {"rows": 3, "content_sha256": "d" * 64},
            "payments": {"rows": 2, "content_sha256": "e" * 64},
        },
    }


def test_signed_manifest_binds_exact_archive_and_snapshot(tmp_path):
    backup = tmp_path / "flashin.sql.gz"
    backup.write_bytes(b"restore-proven-backup")

    manifest = build_manifest(
        backup,
        snapshot(),
        source_database="flashin",
        secret=SECRET,
        created_at="2026-08-05T00:00:00Z",
    )

    assert validate_manifest(manifest, backup, SECRET) == []
    assert manifest["backup"]["size_bytes"] == len(b"restore-proven-backup")
    assert manifest["database_snapshot"] == snapshot()


def test_archive_byte_or_size_change_is_rejected(tmp_path):
    backup = tmp_path / "flashin.sql.gz"
    backup.write_bytes(b"original")
    manifest = build_manifest(
        backup,
        snapshot(),
        source_database="flashin",
        secret=SECRET,
    )

    backup.write_bytes(b"tampered-archive")
    errors = validate_manifest(manifest, backup, SECRET)

    assert "backup SHA-256 does not match signed manifest" in errors
    assert "backup size does not match signed manifest" in errors


def test_manifest_signature_and_snapshot_tampering_are_rejected(tmp_path):
    backup = tmp_path / "flashin.sql.gz"
    backup.write_bytes(b"original")
    manifest = build_manifest(
        backup,
        snapshot(),
        source_database="flashin",
        secret=SECRET,
    )
    manifest["database_snapshot"]["alembic_revision"] = "tampered"

    errors = validate_manifest(manifest, backup, SECRET)

    assert "backup manifest signature is invalid" in errors


def test_snapshot_comparison_detects_schema_revision_and_ledger_changes():
    expected = snapshot()
    actual = copy.deepcopy(expected)
    actual["alembic_revision"] = "0021_previous"
    actual["schema_sha256"] = "f" * 64
    actual["critical_tables"]["orders"]["rows"] = 2
    actual["critical_tables"]["payments"]["content_sha256"] = "0" * 64

    errors = compare_snapshots(expected, actual)

    assert "restored database alembic_revision does not match backup manifest" in errors
    assert "restored database schema_sha256 does not match backup manifest" in errors
    assert "restored critical table orders rows does not match" in errors
    assert "restored critical table payments content_sha256 does not match" in errors


def test_snapshot_comparison_rejects_missing_critical_table():
    expected = snapshot()
    actual = copy.deepcopy(expected)
    del actual["critical_tables"]["payments"]

    errors = compare_snapshots(expected, actual)

    assert "restored critical table set does not match backup manifest" in errors


@pytest.mark.parametrize(
    "value",
    ["", "bad-name", "name;drop database", "template0", "template1", "space name"],
)
def test_database_identifiers_fail_closed(value):
    with pytest.raises(BackupIntegrityError):
        _validate_identifier(value, "database")
