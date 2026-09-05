from datetime import UTC, datetime, timedelta
from pathlib import Path
import os
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from pilot_evidence import (  # noqa: E402
    build_rollback_drill_report,
    configuration_fingerprint,
    release_binding,
    sha256_file,
    sign_payload,
    utc_timestamp,
    validate_provider_report,
    validate_rollback_drill_report,
    verify_payload_signature,
)


def env() -> dict[str, str]:
    return {
        "PILOT_EVIDENCE_SIGNING_SECRET": "e" * 48,
        "APP_ENV": "production",
        "TELEGRAM_BOT_TOKEN": "telegram-token",
        "YOOKASSA_SHOP_ID": "shop",
        "YOOKASSA_SECRET_KEY": "yoo-secret",
        "MOYSKLAD_TOKEN": "moy-token",
        "MEDIA_STORAGE": "r2",
        "S3_BUCKET": "bucket",
        "S3_ACCESS_KEY_ID": "access",
        "S3_SECRET_ACCESS_KEY": "secret",
        "MEILISEARCH_ENABLED": "true",
        "MEILISEARCH_MASTER_KEY": "meili",
    }


def release(name: str = "current", sha: str = "a" * 64) -> dict[str, str]:
    return {
        "release_id": name,
        "git_commit": ("1" if name == "current" else "2") * 40,
        "sha256": sha,
        "promoted_at": "2026-08-03T17:00:00Z",
    }


def provider_report(now: datetime, values: dict[str, str], current: dict[str, str]):
    results = [
        {"name": name, "ok": True, "returncode": 0, "stdout": "", "stderr": ""}
        for name in ("telegram", "yookassa", "moysklad", "r2_s3", "meilisearch")
    ]
    payload = {
        "schema_version": 1,
        "kind": "provider_probes",
        "created_at": utc_timestamp(now),
        "expires_at": utc_timestamp(now + timedelta(minutes=60)),
        "mode": "strict",
        "go": True,
        "configuration_fingerprint": configuration_fingerprint(
            values, values["PILOT_EVIDENCE_SIGNING_SECRET"]
        ),
        "release": release_binding(current),
        "summary": {"total": 5, "passed": 5, "failed": 0},
        "results": results,
    }
    return sign_payload(payload, values["PILOT_EVIDENCE_SIGNING_SECRET"])


def backup_manifest(
    backup: Path,
    values: dict[str, str],
    created_at: datetime,
) -> Path:
    payload = {
        "schema_version": 1,
        "kind": "postgres_backup_manifest",
        "created_at": utc_timestamp(created_at),
        "source_database": "flashin",
        "backup": {
            "sha256": sha256_file(backup),
            "size_bytes": backup.stat().st_size,
        },
        "database_snapshot": {
            "alembic_revision": "test-revision",
            "public_table_count": 1,
            "public_tables_sha256": "1" * 64,
            "schema_sha256": "2" * 64,
            "critical_tables": {},
        },
    }
    path = Path(f"{backup}.manifest.json")
    signed = sign_payload(payload, values["PILOT_EVIDENCE_SIGNING_SECRET"])
    path.write_text(__import__("json").dumps(signed), encoding="utf-8")
    return path


def test_signature_detects_tampering():
    values = env()
    signed = sign_payload({"kind": "test", "value": 1}, values["PILOT_EVIDENCE_SIGNING_SECRET"])
    assert verify_payload_signature(signed, values["PILOT_EVIDENCE_SIGNING_SECRET"])
    signed["value"] = 2
    assert not verify_payload_signature(signed, values["PILOT_EVIDENCE_SIGNING_SECRET"])


def test_provider_evidence_binds_release_configuration_and_time():
    values = env()
    current = release()
    now = datetime(2026, 8, 3, 18, 0, tzinfo=UTC)
    report = provider_report(now, values, current)
    assert not validate_provider_report(
        report, env=values, current_release=current, now=now, max_age_minutes=60
    )

    changed = dict(values)
    changed["YOOKASSA_SHOP_ID"] = "other"
    errors = validate_provider_report(
        report, env=changed, current_release=current, now=now, max_age_minutes=60
    )
    assert any("configuration fingerprint" in item for item in errors)

    errors = validate_provider_report(
        report,
        env=values,
        current_release=release("other", "b" * 64),
        now=now,
        max_age_minutes=60,
    )
    assert any("release" in item for item in errors)

    errors = validate_provider_report(
        report,
        env=values,
        current_release=current,
        now=now + timedelta(minutes=61),
        max_age_minutes=60,
    )
    assert any("expired" in item or "older" in item for item in errors)


def test_provider_evidence_requires_each_probe_exactly_once():
    values = env()
    current = release()
    now = datetime(2026, 8, 3, 18, 0, tzinfo=UTC)
    report = provider_report(now, values, current)
    report["results"].append(dict(report["results"][0]))
    report = sign_payload(report, values["PILOT_EVIDENCE_SIGNING_SECRET"])
    errors = validate_provider_report(
        report, env=values, current_release=current, now=now, max_age_minutes=60
    )
    assert any("exactly once" in item for item in errors)


def test_provider_evidence_shared_validator_rejects_resigned_noisy_results():
    values = env()
    current = release()
    now = datetime(2026, 8, 3, 18, 0, tzinfo=UTC)
    report = provider_report(now, values, current)
    report.pop("signature", None)
    report["results"][0]["stdout"] = "provider-private-body"
    report["results"][0]["provider_body"] = "unexpected-private-field"
    report["results"][1]["returncode"] = 7
    report = sign_payload(report, values["PILOT_EVIDENCE_SIGNING_SECRET"])

    errors = validate_provider_report(
        report, env=values, current_release=current, now=now, max_age_minutes=60
    )

    assert "provider evidence must not retain probe stdout/stderr" in errors
    assert "provider evidence result contains unsupported fields" in errors
    assert "passing provider evidence result must have returncode 0" in errors
    assert not any("signature is invalid" in item for item in errors)


def test_rollback_drill_evidence_detects_backup_tampering(tmp_path: Path):
    values = env()
    backup = tmp_path / "backup.sql.gz"
    backup.write_bytes(b"valid-backup")
    now = datetime(2026, 8, 3, 18, 0, tzinfo=UTC)
    report = build_rollback_drill_report(
        from_release=release("from", "a" * 64),
        to_release=release("to", "b" * 64),
        backup_path=backup,
        env=values,
        completed_at=now,
        max_age_days=30,
    )
    assert report["recovery"]["scope"] == "ci"
    assert not validate_rollback_drill_report(
        report, env=values, now=now, max_age_days=30
    )
    backup.write_bytes(b"tampered")
    errors = validate_rollback_drill_report(
        report, env=values, now=now, max_age_days=30
    )
    assert any("checksum" in item or "size" in item for item in errors)


def test_ci_and_legacy_recovery_evidence_cannot_satisfy_pilot_host_gate(tmp_path: Path):
    values = env()
    backup = tmp_path / "backup.sql.gz"
    backup.write_bytes(b"backup")
    now = datetime(2026, 8, 3, 18, 0, tzinfo=UTC)
    report = build_rollback_drill_report(
        from_release=release("from", "a" * 64),
        to_release=release("to", "b" * 64),
        backup_path=backup,
        env=values,
        completed_at=now,
    )
    assert validate_rollback_drill_report(report, env=values, now=now) == []
    errors = validate_rollback_drill_report(
        report,
        env=values,
        now=now,
        required_scope="pilot_host",
        expected_host_id="pilot-host-a",
        expected_rto_target_seconds=120,
        expected_rpo_target_seconds=3600,
    )
    assert any("scope must be pilot_host" in item for item in errors)

    legacy = dict(report)
    legacy.pop("signature", None)
    legacy.pop("recovery", None)
    legacy = sign_payload(legacy, values["PILOT_EVIDENCE_SIGNING_SECRET"])
    assert validate_rollback_drill_report(legacy, env=values, now=now) == []
    errors = validate_rollback_drill_report(
        legacy,
        env=values,
        now=now,
        required_scope="pilot_host",
        expected_host_id="pilot-host-a",
        expected_rto_target_seconds=120,
        expected_rpo_target_seconds=3600,
    )
    assert any("missing required pilot_host" in item for item in errors)


def test_pilot_host_recovery_binds_host_targets_and_signed_backup_age(tmp_path: Path):
    values = env()
    backup = tmp_path / "backup.sql.gz"
    backup.write_bytes(b"backup")
    now = datetime(2026, 8, 3, 18, 0, tzinfo=UTC)
    manifest = backup_manifest(backup, values, now - timedelta(minutes=10))
    report = build_rollback_drill_report(
        from_release=release("from", "a" * 64),
        to_release=release("to", "b" * 64),
        backup_path=backup,
        env=values,
        started_at=now - timedelta(seconds=30),
        completed_at=now,
        recovery_scope="pilot_host",
        recovery_host_id="pilot-host-a",
        rto_target_seconds=120,
        rpo_target_seconds=3600,
        backup_manifest_path=manifest,
    )
    assert report["result"] == "GO"
    assert report["recovery"]["rto_met"] is True
    assert report["recovery"]["rpo_met"] is True
    assert validate_rollback_drill_report(
        report,
        env=values,
        now=now,
        required_scope="pilot_host",
        expected_host_id="pilot-host-a",
        expected_rto_target_seconds=120,
        expected_rpo_target_seconds=3600,
    ) == []

    errors = validate_rollback_drill_report(
        report,
        env=values,
        now=now,
        required_scope="pilot_host",
        expected_host_id="different-host",
        expected_rto_target_seconds=120,
        expected_rpo_target_seconds=3600,
    )
    assert any("fingerprint does not match" in item for item in errors)


def test_pilot_host_recovery_misses_rto_and_rpo_fail_closed(tmp_path: Path):
    values = env()
    now = datetime(2026, 8, 3, 18, 0, tzinfo=UTC)

    rto_backup = tmp_path / "rto.sql.gz"
    rto_backup.write_bytes(b"rto-backup")
    rto_manifest = backup_manifest(rto_backup, values, now - timedelta(minutes=5))
    rto_report = build_rollback_drill_report(
        from_release=release("from", "a" * 64),
        to_release=release("to", "b" * 64),
        backup_path=rto_backup,
        env=values,
        started_at=now - timedelta(seconds=121),
        completed_at=now,
        recovery_scope="pilot_host",
        recovery_host_id="pilot-host-a",
        rto_target_seconds=120,
        rpo_target_seconds=3600,
        backup_manifest_path=rto_manifest,
    )
    assert rto_report["result"] == "NO-GO"
    errors = validate_rollback_drill_report(
        rto_report,
        env=values,
        now=now,
        required_scope="pilot_host",
        expected_host_id="pilot-host-a",
        expected_rto_target_seconds=120,
        expected_rpo_target_seconds=3600,
    )
    assert any("RTO target was missed" in item for item in errors)

    rpo_backup = tmp_path / "rpo.sql.gz"
    rpo_backup.write_bytes(b"rpo-backup")
    rpo_manifest = backup_manifest(rpo_backup, values, now - timedelta(hours=2))
    os.utime(rpo_backup, None)
    os.utime(rpo_manifest, None)
    rpo_report = build_rollback_drill_report(
        from_release=release("from", "a" * 64),
        to_release=release("to", "b" * 64),
        backup_path=rpo_backup,
        env=values,
        started_at=now - timedelta(seconds=10),
        completed_at=now,
        recovery_scope="pilot_host",
        recovery_host_id="pilot-host-a",
        rto_target_seconds=120,
        rpo_target_seconds=3600,
        backup_manifest_path=rpo_manifest,
    )
    assert rpo_report["result"] == "NO-GO"
    errors = validate_rollback_drill_report(
        rpo_report,
        env=values,
        now=now,
        required_scope="pilot_host",
        expected_host_id="pilot-host-a",
        expected_rto_target_seconds=120,
        expected_rpo_target_seconds=3600,
    )
    assert any("RPO target was missed" in item for item in errors)


def test_pilot_host_recovery_tampering_breaks_signature(tmp_path: Path):
    values = env()
    backup = tmp_path / "backup.sql.gz"
    backup.write_bytes(b"backup")
    now = datetime(2026, 8, 3, 18, 0, tzinfo=UTC)
    manifest = backup_manifest(backup, values, now - timedelta(minutes=5))
    report = build_rollback_drill_report(
        from_release=release("from", "a" * 64),
        to_release=release("to", "b" * 64),
        backup_path=backup,
        env=values,
        started_at=now - timedelta(seconds=20),
        completed_at=now,
        recovery_scope="pilot_host",
        recovery_host_id="pilot-host-a",
        rto_target_seconds=120,
        rpo_target_seconds=3600,
        backup_manifest_path=manifest,
    )
    report["recovery"]["rto_target_seconds"] = 999
    errors = validate_rollback_drill_report(
        report,
        env=values,
        now=now,
        required_scope="pilot_host",
        expected_host_id="pilot-host-a",
        expected_rto_target_seconds=120,
        expected_rpo_target_seconds=3600,
    )
    assert any("signature is invalid" in item for item in errors)
