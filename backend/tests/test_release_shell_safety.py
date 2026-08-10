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


def test_deploy_requires_retained_artifact_before_runtime_mutation_and_promotes_after_smoke():
    script = read("scripts/deploy_production.sh")
    gate_pos = script.index("deploy_release_gate.py --archive")
    inspect_pos = script.index("pilot_release_capability.py inspect --archive")
    extract_pos = script.index("release_control.py extract")
    stop_pos = script.index("pilot_runtime.py _stop")
    build_pos = script.index("Building images from verified immutable Release artifact")
    migrate_pos = script.index("alembic.ini upgrade head")
    smoke_pos = script.index("container_smoke.py")
    promote_pos = script.index("release_control.py promote")

    assert gate_pos < inspect_pos < extract_pos < stop_pos < build_pos < migrate_pos < smoke_pos < promote_pos
    assert "release_control.py create" not in script
    assert "RELEASE=deploy/release/builds/flashin_<release>.zip make deploy-prod" in script
    assert 'cd "$release_source_dir"' in script
    assert "backup_postgres.sh --print-path" in script
    assert 'verify_backup.sh "$backup_file"' in script
    assert "scripts/rollback.sh previous" in script


def test_release_and_pilot_runtime_artifacts_are_gitignored():
    ignored = read(".gitignore")
    assert "deploy/release/builds/" in ignored
    assert "deploy/release/runtime/" in ignored
    assert "docs/pilot/rollback_drill_report.json" in ignored
    assert "docs/pilot/pilot_admission_manifest.json" in ignored
