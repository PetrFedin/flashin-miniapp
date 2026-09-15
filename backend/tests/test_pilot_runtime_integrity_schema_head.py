from pathlib import Path

from scripts.check_pilot_runtime_integrity import revisions_match_release_head


ROOT = Path(__file__).resolve().parents[2]
ROLLBACK = ROOT / "scripts" / "rollback.sh"
RUNTIME_INTEGRITY = ROOT / "scripts" / "check_pilot_runtime_integrity.py"


def test_schema_revision_requires_exact_target_release_head():
    assert revisions_match_release_head({"0041_reverse_logistics_authority"}, {"0041_reverse_logistics_authority"}) is True
    assert revisions_match_release_head({"0040_delivery_authority"}, {"0041_reverse_logistics_authority"}) is False
    assert revisions_match_release_head({"0041_reverse_logistics_authority"}, {"0040_delivery_authority"}) is False
    assert revisions_match_release_head(set(), set()) is False


def test_schema_revision_supports_exact_multi_head_set_without_subset_acceptance():
    expected = {"head_a", "head_b"}
    assert revisions_match_release_head(expected, {"head_a", "head_b"}) is True
    assert revisions_match_release_head(expected, {"head_a"}) is False
    assert revisions_match_release_head(expected, {"head_a", "head_b", "head_c"}) is False


def test_runtime_integrity_cli_requires_release_head_while_library_audit_remains_reusable():
    source = RUNTIME_INTEGRITY.read_text(encoding="utf-8")

    assert "ScriptDirectory.from_config(config).get_heads()" in source
    assert 'SELECT version_num FROM alembic_version' in source
    assert "if require_release_head:" in source
    assert "_assert_database_at_release_head(connection)" in source
    assert "run_audit(connection, require_release_head=True)" in source
    assert '"reason": "alembic_revision_mismatch"' in source


def test_rollback_runs_schema_head_guard_before_public_services_start():
    source = ROLLBACK.read_text(encoding="utf-8")
    guard = 'docker compose run --rm backend python scripts/check_pilot_runtime_integrity.py'
    public_start = 'echo "Starting rolled-back production services, durable provider worker and monitoring..."'

    assert guard in source
    assert public_start in source
    assert source.index(guard) < source.index(public_start)
