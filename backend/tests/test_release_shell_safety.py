from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_rollback_protects_server_state_and_requires_explicit_code_only_mode():
    script = read("scripts/rollback.sh")
    for protected in (
        "--exclude '.git/'",
        "--exclude '.env'",
        "--exclude 'backups/'",
        "--exclude 'media/'",
        "--exclude 'deploy/release/builds/'",
        "--exclude 'deploy/release/runtime/'",
        "--exclude 'docs/pilot/rollback_drill_report.json'",
        "--exclude 'docs/pilot/pilot_admission_manifest.json'",
    ):
        assert protected in script
    assert "ALLOW_CODE_ONLY_ROLLBACK" in script
    assert '"$CONTROL_SCRIPT" verify' in script
    assert 'release_control.py" extract' in script


def test_rollback_drill_is_explicit_signed_and_database_backed():
    script = read("scripts/rollback.sh")
    assert "ROLLBACK_DRILL" in script
    assert "A rollback drill must restore a verified database backup" in script
    assert "pilot_evidence.py" in script
    assert "validate-secret" in script
    assert "record-rollback" in script
    assert "from_release.json" in script


def test_restore_is_destructive_only_after_validation_and_recreates_database():
    script = read("scripts/restore_postgres.sh")
    assert "gzip -t" in script
    assert "Refusing restore while" in script
    assert "DROP DATABASE IF EXISTS" in script
    assert "CREATE DATABASE" in script
    assert "alembic_version" in script
    assert "--yes" in script


def test_deploy_creates_inspects_and_promotes_verified_release_and_backup():
    script = read("scripts/deploy_production.sh")
    create_pos = script.index("release_control.py create")
    inspect_pos = script.index("pilot_release_capability.py inspect --archive")
    migrate_pos = script.index("alembic.ini upgrade head")
    promote_pos = script.index("release_control.py promote")
    assert create_pos < inspect_pos < migrate_pos < promote_pos
    assert "backup_postgres.sh --print-path" in script
    assert 'verify_backup.sh "$backup_file"' in script
    assert "scripts/rollback.sh previous" in script


def test_release_and_pilot_runtime_artifacts_are_gitignored():
    ignored = read(".gitignore")
    assert "deploy/release/builds/" in ignored
    assert "deploy/release/runtime/" in ignored
    assert "docs/pilot/rollback_drill_report.json" in ignored
    assert "docs/pilot/pilot_admission_manifest.json" in ignored
