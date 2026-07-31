from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
ADMIN = ROOT / "admin"


def _read(relative: str) -> str:
    return (ADMIN / relative).read_text(encoding="utf-8")


def test_admin_bootstrap_mounts_existing_app_and_isolated_jobs_console():
    index = _read("index.html")
    bootstrap = _read("src/bootstrap.jsx")

    assert '<div id="root"></div>' in index
    assert '<div id="scheduled-jobs-root"></div>' in index
    assert 'src="/src/bootstrap.jsx"' in index
    assert 'src="/src/main.jsx"' not in index
    assert 'import "./main.jsx";' in bootstrap
    assert 'import ScheduledJobsPanel from "./ScheduledJobsPanel";' in bootstrap
    assert 'document.getElementById("scheduled-jobs-root")' in bootstrap


def test_jobs_console_is_hidden_without_current_admin_session():
    bootstrap = _read("src/bootstrap.jsx")

    assert 'localStorage.getItem("admin_token")' in bootstrap
    assert 'if (!token) return null;' in bootstrap
    assert 'key={token}' in bootstrap
    assert 'Административная сессия завершена' in bootstrap
    assert 'Authorization: `Bearer ${currentToken}`' in bootstrap


def test_jobs_console_uses_only_allowlisted_backend_routes():
    panel = _read("src/ScheduledJobsPanel.jsx")

    assert 'api("/api/ops/jobs/definitions")' in panel
    assert 'api(`/api/ops/jobs/runs?${params.toString()}`)' in panel
    assert 'api(`/api/ops/jobs/runs/${runId}`)' in panel
    assert 'api(`/api/ops/jobs/${encodeURIComponent(name)}/run`' in panel
    assert 'api(`/api/ops/jobs/runs/${run.id}/retry`' in panel
    assert "module" not in panel.lower()
    assert "callable" not in panel.lower()
    assert "function_name" not in panel


def test_jobs_console_guards_duplicate_and_unintended_actions():
    panel = _read("src/ScheduledJobsPanel.jsx")

    assert "if (!definition?.manual_enabled || actionKey) return;" in panel
    assert "if (!run || ![\"failed\", \"skipped\"].includes(run.status) || actionKey) return;" in panel
    assert "window.confirm" in panel
    assert "disabled={loading || Boolean(actionKey)}" in panel
    assert "disabled={!definition.manual_enabled || Boolean(actionKey)}" in panel
    assert "definition?.retry_enabled" in panel


def test_jobs_console_handles_stale_responses_errors_details_and_pagination():
    panel = _read("src/ScheduledJobsPanel.jsx")

    assert "requestVersion.current" in panel
    assert "if (version !== requestVersion.current) return;" in panel
    assert 'role="alert"' in panel
    assert 'role="status"' in panel
    assert 'aria-live="polite"' in panel
    assert 'limit: "25"' in panel
    assert "Math.max(pages, 1)" in panel
    assert ".slice(0, 8000)" in panel
    assert ".slice(0, 2000)" in panel


def test_jobs_console_styles_are_scoped_and_responsive():
    styles = _read("src/scheduled-jobs.css")

    assert ".scheduled-jobs-host" in styles
    assert ".scheduled-jobs__table-wrap" in styles
    assert "overflow-x: auto" in styles
    assert ".scheduled-jobs__status--succeeded" in styles
    assert ".scheduled-jobs__status--failed" in styles
    assert ".scheduled-jobs__status--running" in styles
    assert "@media (max-width: 620px)" in styles
