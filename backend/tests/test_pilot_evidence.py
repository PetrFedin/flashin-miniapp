from datetime import UTC, datetime, timedelta
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from pilot_evidence import (  # noqa: E402
    build_rollback_drill_report,
    configuration_fingerprint,
    release_binding,
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
    assert not validate_rollback_drill_report(
        report, env=values, now=now, max_age_days=30
    )
    backup.write_bytes(b"tampered")
    errors = validate_rollback_drill_report(
        report, env=values, now=now, max_age_days=30
    )
    assert any("checksum" in item or "size" in item for item in errors)
