import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from pilot_readiness import CheckResult, build_report, check_legal_document, read_env, render_markdown


def test_read_env_ignores_comments_and_unquotes_values(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "# comment\nAPP_ENV=production\nADMIN_URL='https://admin.example'\nEMPTY=\n",
        encoding="utf-8",
    )

    assert read_env(env_file) == {
        "APP_ENV": "production",
        "ADMIN_URL": "https://admin.example",
        "EMPTY": "",
    }


def test_legal_template_is_a_critical_failure(tmp_path):
    relative = "frontend/public/legal/offer.html"
    path = tmp_path / relative
    path.parent.mkdir(parents=True)
    path.write_text(
        "<html><body>Настоящий документ является шаблоном и должен быть проверен юристом."
        + " Описание условий." * 60
        + "</body></html>",
        encoding="utf-8",
    )

    result = check_legal_document(tmp_path, relative)

    assert result.ok is False
    assert result.critical is True
    assert "placeholder markers" in result.detail


def test_final_legal_document_passes(tmp_path):
    relative = "frontend/public/legal/privacy.html"
    path = tmp_path / relative
    path.parent.mkdir(parents=True)
    path.write_text(
        "<html><body><h1>Политика обработки персональных данных</h1>"
        + " Оператор обрабатывает данные покупателя для исполнения договора, доставки и поддержки."
        * 20
        + "</body></html>",
        encoding="utf-8",
    )

    result = check_legal_document(tmp_path, relative)

    assert result.ok is True
    assert result.detail == "final text detected"


def test_report_is_no_go_on_any_critical_failure():
    report = build_report(
        "live",
        [
            CheckResult("ready", True, True, "200"),
            CheckResult("legal", False, True, "placeholder"),
            CheckResult("metrics", False, False, "not configured"),
        ],
    )

    assert report["go"] is False
    assert report["summary"] == {
        "total": 3,
        "passed": 1,
        "critical_failed": 1,
        "optional_failed": 1,
    }
    markdown = render_markdown(report)
    assert "Decision: **NO-GO**" in markdown
    assert "`legal`" in markdown
