from datetime import UTC, datetime, timedelta
from pathlib import Path
import json
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from pilot_admission import (  # noqa: E402
    build_manifest,
    validate_admission_evidence_inputs,
    validate_admission_manifest,
    validate_live_gate_report,
)
from readiness_gate import build_signed_live_report  # noqa: E402
from pilot_evidence import (  # noqa: E402
    build_rollback_drill_report,
    configuration_fingerprint,
    release_binding,
    sha256_file,
    sign_payload,
    utc_timestamp,
)


def env():
    return {
        "PILOT_EVIDENCE_SIGNING_SECRET": "k" * 48,
        "APP_ENV": "production",
        "TELEGRAM_BOT_TOKEN": "telegram",
        "YOOKASSA_SHOP_ID": "shop",
        "YOOKASSA_SECRET_KEY": "yoo",
        "MOYSKLAD_TOKEN": "moy",
        "MEDIA_STORAGE": "r2",
        "S3_BUCKET": "bucket",
        "S3_ACCESS_KEY_ID": "access",
        "S3_SECRET_ACCESS_KEY": "secret",
        "MEILISEARCH_ENABLED": "true",
        "MEILISEARCH_MASTER_KEY": "meili",
        "PILOT_RECOVERY_SCOPE": "pilot_host",
        "PILOT_RECOVERY_HOST_ID": "pilot-host-test",
        "PILOT_RECOVERY_RTO_SECONDS": "600",
        "PILOT_RECOVERY_RPO_SECONDS": "3600",
    }


def release(release_id: str, char: str):
    return {
        "release_id": release_id,
        "git_commit": char * 40,
        "sha256": char * 64,
        "promoted_at": "2026-08-03T17:00:00Z",
    }


def provider_report(now, values, current):
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


def live_report(now, values, current):
    unsigned = {
        "phase": "live",
        "go": True,
        "summary": {"total": 10, "passed": 10, "critical_failed": 0, "optional_failed": 0},
        "critical_failed": [],
        "checks": [
            {"name": "live:api_ready", "ok": True, "critical": True, "detail": "ok"},
            {"name": "live:provider_integrations", "ok": True, "critical": True, "detail": "ok"},
        ],
    }
    report = build_signed_live_report(
        unsigned,
        env=values,
        current_release=current,
        max_age_minutes=30,
    )
    report["created_at"] = utc_timestamp(now)
    report["generated_at"] = utc_timestamp(now)
    report["expires_at"] = utc_timestamp(now + timedelta(minutes=30))
    return sign_payload(report, values["PILOT_EVIDENCE_SIGNING_SECRET"])


def write_json(path: Path, payload):
    path.write_text(json.dumps(payload), encoding="utf-8")


def signed_backup_manifest(backup: Path, values, created_at: datetime) -> Path:
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
    manifest = Path(f"{backup}.manifest.json")
    write_json(manifest, sign_payload(payload, values["PILOT_EVIDENCE_SIGNING_SECRET"]))
    return manifest


def pilot_rollback_report(
    tmp_path: Path,
    values,
    from_release,
    to_release,
    now: datetime,
    name: str = "backup",
):
    backup = tmp_path / f"{name}.sql.gz"
    backup.write_bytes(f"{name}-backup".encode("utf-8"))
    manifest = signed_backup_manifest(backup, values, now - timedelta(minutes=5))
    return build_rollback_drill_report(
        from_release=from_release,
        to_release=to_release,
        backup_path=backup,
        env=values,
        started_at=now - timedelta(seconds=30),
        completed_at=now,
        recovery_scope="pilot_host",
        recovery_host_id=values["PILOT_RECOVERY_HOST_ID"],
        rto_target_seconds=values["PILOT_RECOVERY_RTO_SECONDS"],
        rpo_target_seconds=values["PILOT_RECOVERY_RPO_SECONDS"],
        backup_manifest_path=manifest,
        max_age_days=30,
    )


def test_admission_manifest_binds_all_evidence_and_approvals(tmp_path: Path):
    values = env()
    current = release("current", "a")
    previous = release("previous", "b")
    now = datetime.now(UTC)
    provider_path = tmp_path / "provider.json"
    live_path = tmp_path / "live.json"
    rollback_path = tmp_path / "rollback.json"
    write_json(provider_path, provider_report(now, values, current))
    write_json(live_path, live_report(now, values, current))
    write_json(
        rollback_path,
        pilot_rollback_report(tmp_path, values, current, previous, now),
    )
    manifest = build_manifest(
        env=values,
        current_release=current,
        previous_release=previous,
        provider_report_path=provider_path,
        live_report_path=live_path,
        rollback_report_path=rollback_path,
        approvals={
            "business_owner": "Business",
            "operations_owner": "Operations",
            "technical_owner": "Technical",
            "legal_owner": "Legal",
            "support_owner": "Support",
        },
        acknowledgements={
            "legal_documents_approved": True,
            "support_process_ready": True,
            "rollback_drill_completed": True,
            "provider_probe_side_effect_understood": True,
            "pilot_scope_limited_to_20_orders": True,
        },
        max_age_minutes=60,
    )
    assert not validate_admission_manifest(
        manifest,
        env=values,
        current_release=current,
        previous_release=previous,
        admission_max_age_minutes=60,
        provider_max_age_minutes=60,
        live_max_age_minutes=30,
        rollback_max_age_days=30,
    )

    live_path.write_text("{}", encoding="utf-8")
    errors = validate_admission_manifest(
        manifest,
        env=values,
        current_release=current,
        previous_release=previous,
        admission_max_age_minutes=60,
        provider_max_age_minutes=60,
        live_max_age_minutes=30,
        rollback_max_age_days=30,
    )
    assert any("checksum" in item for item in errors)


def test_live_gate_requires_provider_evidence_and_fresh_timestamp():
    now = datetime.now(UTC)
    values = env()
    current = release("current", "a")
    report = live_report(now, values, current)
    assert validate_live_gate_report(
        report, env=values, current_release=current, max_age_minutes=30
    ) == []
    report["checks"] = []
    errors = validate_live_gate_report(
        report, env=values, current_release=current, max_age_minutes=30
    )
    assert any("provider evidence" in item for item in errors)
    old = live_report(now - timedelta(minutes=31), values, current)
    errors = validate_live_gate_report(
        old, env=values, current_release=current, max_age_minutes=30
    )
    assert any("expired" in item or "older" in item for item in errors)


def test_admission_rejects_rollback_drill_for_unrelated_releases(tmp_path: Path):
    values = env()
    current = release("current", "a")
    previous = release("previous", "b")
    unrelated = release("unrelated", "c")
    now = datetime.now(UTC)
    provider_path = tmp_path / "provider.json"
    live_path = tmp_path / "live.json"
    rollback_path = tmp_path / "rollback.json"
    write_json(provider_path, provider_report(now, values, current))
    write_json(live_path, live_report(now, values, current))
    write_json(
        rollback_path,
        pilot_rollback_report(tmp_path, values, unrelated, previous, now),
    )
    manifest = build_manifest(
        env=values,
        current_release=current,
        previous_release=previous,
        provider_report_path=provider_path,
        live_report_path=live_path,
        rollback_report_path=rollback_path,
        approvals={
            "business_owner": "Business",
            "operations_owner": "Operations",
            "technical_owner": "Technical",
            "legal_owner": "Legal",
            "support_owner": "Support",
        },
        acknowledgements={key: True for key in (
            "legal_documents_approved",
            "support_process_ready",
            "rollback_drill_completed",
            "provider_probe_side_effect_understood",
            "pilot_scope_limited_to_20_orders",
        )},
        max_age_minutes=60,
    )
    errors = validate_admission_manifest(
        manifest,
        env=values,
        current_release=current,
        previous_release=previous,
        admission_max_age_minutes=60,
        provider_max_age_minutes=60,
        live_max_age_minutes=30,
        rollback_max_age_days=30,
    )
    assert any("rollback drill origin" in item for item in errors)


def test_live_gate_rejects_tampering_configuration_and_other_release():
    now = datetime.now(UTC)
    values = env()
    current = release("current", "a")
    report = live_report(now, values, current)

    tampered = dict(report)
    tampered["go"] = False
    errors = validate_live_gate_report(
        tampered, env=values, current_release=current, max_age_minutes=30
    )
    assert any("signature" in item for item in errors)

    changed_env = dict(values)
    changed_env["MEILISEARCH_MASTER_KEY"] = "different"
    errors = validate_live_gate_report(
        report, env=changed_env, current_release=current, max_age_minutes=30
    )
    assert any("configuration fingerprint" in item for item in errors)

    other = release("other", "c")
    errors = validate_live_gate_report(
        report, env=values, current_release=other, max_age_minutes=30
    )
    assert any("live gate release" in item for item in errors)


def test_admission_create_preflight_binds_live_gate_to_current_release(tmp_path: Path):
    values = env()
    current = release("current", "a")
    previous = release("previous", "b")
    unrelated = release("unrelated", "c")
    now = datetime.now(UTC)
    provider = provider_report(now, values, current)
    live = live_report(now, values, current)
    rollback = pilot_rollback_report(tmp_path, values, current, previous, now)

    assert validate_admission_evidence_inputs(
        provider,
        live,
        rollback,
        env=values,
        current_release=current,
        provider_max_age_minutes=60,
        live_max_age_minutes=30,
        rollback_max_age_days=30,
    ) == []

    unrelated_live = live_report(now, values, unrelated)
    errors = validate_admission_evidence_inputs(
        provider,
        unrelated_live,
        rollback,
        env=values,
        current_release=current,
        provider_max_age_minutes=60,
        live_max_age_minutes=30,
        rollback_max_age_days=30,
    )
    assert any("live gate release" in item for item in errors)


def test_admission_preflight_rejects_resigned_provider_output(tmp_path: Path):
    values = env()
    current = release("current", "a")
    previous = release("previous", "b")
    now = datetime.now(UTC)
    provider = provider_report(now, values, current)
    provider.pop("signature", None)
    provider["results"][0]["stderr"] = "private-provider-error-body"
    provider["results"][0]["provider_reference"] = "private-provider-id"
    provider = sign_payload(provider, values["PILOT_EVIDENCE_SIGNING_SECRET"])
    live = live_report(now, values, current)
    rollback = pilot_rollback_report(tmp_path, values, current, previous, now)

    errors = validate_admission_evidence_inputs(
        provider,
        live,
        rollback,
        env=values,
        current_release=current,
        provider_max_age_minutes=60,
        live_max_age_minutes=30,
        rollback_max_age_days=30,
    )

    assert "provider evidence must not retain probe stdout/stderr" in errors
    assert "provider evidence result contains unsupported fields" in errors


def test_admission_rejects_ci_recovery_evidence(tmp_path: Path):
    values = env()
    current = release("current", "a")
    previous = release("previous", "b")
    now = datetime.now(UTC)
    provider = provider_report(now, values, current)
    live = live_report(now, values, current)
    backup = tmp_path / "ci-backup.sql.gz"
    backup.write_bytes(b"ci-backup")
    rollback = build_rollback_drill_report(
        from_release=current,
        to_release=previous,
        backup_path=backup,
        env=values,
        completed_at=now,
        recovery_scope="ci",
    )

    errors = validate_admission_evidence_inputs(
        provider,
        live,
        rollback,
        env=values,
        current_release=current,
        provider_max_age_minutes=60,
        live_max_age_minutes=30,
        rollback_max_age_days=30,
    )
    assert any("scope must be pilot_host" in item for item in errors)


def test_admission_requires_explicit_recovery_host_and_targets(tmp_path: Path):
    values = env()
    current = release("current", "a")
    previous = release("previous", "b")
    now = datetime.now(UTC)
    provider = provider_report(now, values, current)
    live = live_report(now, values, current)
    rollback = pilot_rollback_report(tmp_path, values, current, previous, now)

    missing = dict(values)
    missing["PILOT_RECOVERY_SCOPE"] = "ci"
    missing["PILOT_RECOVERY_HOST_ID"] = ""
    missing["PILOT_RECOVERY_RTO_SECONDS"] = "0"
    missing["PILOT_RECOVERY_RPO_SECONDS"] = "not-a-number"
    errors = validate_admission_evidence_inputs(
        provider,
        live,
        rollback,
        env=missing,
        current_release=current,
        provider_max_age_minutes=60,
        live_max_age_minutes=30,
        rollback_max_age_days=30,
    )
    assert "PILOT_RECOVERY_SCOPE must be pilot_host for pilot admission" in errors
    assert "PILOT_RECOVERY_HOST_ID is required for pilot admission" in errors
    assert "PILOT_RECOVERY_RTO_SECONDS must be a positive integer" in errors
    assert "PILOT_RECOVERY_RPO_SECONDS must be a positive integer" in errors
