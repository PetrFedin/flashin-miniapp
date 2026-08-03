from datetime import UTC, datetime, timedelta
from pathlib import Path
import json
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from pilot_admission import (  # noqa: E402
    build_manifest,
    validate_admission_manifest,
    validate_live_gate_report,
)
from pilot_evidence import (  # noqa: E402
    build_rollback_drill_report,
    configuration_fingerprint,
    release_binding,
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
        {"name": name, "ok": True, "returncode": 0, "stdout": "ok", "stderr": ""}
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


def live_report(now):
    return {
        "phase": "live",
        "go": True,
        "generated_at": utc_timestamp(now),
        "summary": {"total": 10, "passed": 10, "critical_failed": 0, "optional_failed": 0},
        "critical_failed": [],
        "checks": [
            {"name": "live:api_ready", "ok": True, "critical": True, "detail": "ok"},
            {"name": "live:provider_integrations", "ok": True, "critical": True, "detail": "ok"},
        ],
    }


def write_json(path: Path, payload):
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_admission_manifest_binds_all_evidence_and_approvals(tmp_path: Path):
    values = env()
    current = release("current", "a")
    previous = release("previous", "b")
    now = datetime.now(UTC)
    provider_path = tmp_path / "provider.json"
    live_path = tmp_path / "live.json"
    rollback_path = tmp_path / "rollback.json"
    backup = tmp_path / "backup.sql.gz"
    backup.write_bytes(b"backup")
    write_json(provider_path, provider_report(now, values, current))
    write_json(live_path, live_report(now))
    write_json(
        rollback_path,
        build_rollback_drill_report(
            from_release=current,
            to_release=previous,
            backup_path=backup,
            env=values,
            completed_at=now,
            max_age_days=30,
        ),
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
    report = live_report(now)
    assert validate_live_gate_report(report, max_age_minutes=30) == []
    report["checks"] = []
    errors = validate_live_gate_report(report, max_age_minutes=30)
    assert any("provider evidence" in item for item in errors)
    old = live_report(now - timedelta(minutes=31))
    errors = validate_live_gate_report(old, max_age_minutes=30)
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
    backup = tmp_path / "backup.sql.gz"
    backup.write_bytes(b"backup")
    write_json(provider_path, provider_report(now, values, current))
    write_json(live_path, live_report(now))
    write_json(
        rollback_path,
        build_rollback_drill_report(
            from_release=unrelated,
            to_release=previous,
            backup_path=backup,
            env=values,
            completed_at=now,
            max_age_days=30,
        ),
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
