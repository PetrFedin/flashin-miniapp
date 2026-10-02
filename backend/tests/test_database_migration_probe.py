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
