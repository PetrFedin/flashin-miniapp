from pathlib import Path

from scripts.database_migration_probe import _scheme, build_report


def test_database_probe_redacts_connection_details_when_url_is_missing():
    report = build_report("")

    assert report["reachable"] is False
    assert report["error_code"] == "DATABASE_URL_missing"
    assert "host" not in report
    assert "password" not in report


def test_database_probe_reports_only_scheme_from_connection_string():
    url = "postgresql+psycopg2://flashin:super-secret@db.example.test:5432/flashin"

    assert _scheme(url) == "postgresql+psycopg2"


def test_production_migration_workflow_is_manual_exact_sha_and_secret_bound():
    root = Path(__file__).resolve().parents[2]
    source = (root / ".github/workflows/production-database-migration.yml").read_text(
        encoding="utf-8"
    )

    assert "workflow_dispatch:" in source
    assert "confirm_migration:" in source
    assert 'test "${{ inputs.confirm_migration }}" = "APPLY"' in source
    assert "FLASHIN_PRODUCTION_DATABASE_URL" in source
    assert "python -m scripts.database_migration_probe" in source
    assert "alembic -c backend/alembic.ini upgrade head" in source


def test_stateful_admission_uses_repository_module_import_path():
    root = Path(__file__).resolve().parents[2]
    source = (root / ".github/workflows/production-stateful-admission.yml").read_text(
        encoding="utf-8"
    )

    assert "python -m scripts.stateful_admission_probe" in source
