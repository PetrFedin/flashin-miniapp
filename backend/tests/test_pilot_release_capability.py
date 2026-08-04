import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from pilot_evidence import sign_payload  # noqa: E402
from pilot_release_capability import (  # noqa: E402
    CAPABILITY_VERSION,
    REQUIRED_FILES,
    capability_payload,
    inspect_runtime_guard,
    validate_capability,
)
from release_control import create_release  # noqa: E402


def _release_state():
    return {
        "release_id": "release-guarded",
        "git_commit": "a" * 40,
        "sha256": "b" * 64,
    }


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
    )


def _guarded_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "pilot@example.com")
    _git(repo, "config", "user.name", "Pilot Test")
    for relative in REQUIRED_FILES:
        path = repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        content = "guarded\n"
        if relative == "backend/api/orders.py":
            content = "acquire_pilot_checkout()\nrecord_pilot_order()\n"
        elif relative == "backend/services/pilot_circuit_breaker.py":
            content = (
                "def stop_pilot_for_order():\n"
                "    pass\n"
                "def trip_pilot_circuit_breaker():\n"
                "    pass\n"
            )
        elif relative == "backend/api/payments.py":
            content = (
                "class ProviderPaymentIntegrityError: pass\n"
                "trip_pilot_circuit_breaker()\n"
                "stop_pilot_for_order()\n"
            )
        elif relative == "backend/api/returns.py":
            content = "trip_pilot_circuit_breaker()\nstop_pilot_for_order()\n"
        elif relative == "backend/services/payment_reconciliation.py":
            content = "payment_reconciliation_mismatch\nstop_pilot_for_order()\n"
        elif relative == "backend/main.py":
            content = (
                "from .middleware.metrics import collect_pilot_metrics, metrics_response\n"
                '@app.get("/metrics", include_in_schema=False)\n'
                "def metrics(db):\n"
                "    collect_pilot_metrics(db, settings)\n"
                "    return metrics_response()\n"
            )
        elif relative == "backend/middleware/metrics.py":
            content = (
                'PILOT = "flashin_pilot_metrics_collection_success"\n'
                "def collect_pilot_metrics(db, settings):\n"
                "    return True\n"
                "def _metric_path(request):\n"
                '    return "__unmatched__"\n'
            )
        elif relative == "deploy/monitoring/rules/flashin_pilot.yml":
            content = (
                "FlashinPilotMetricsUnavailable\n"
                "FlashinPilotArtifactIntegrityFailed\n"
                "FlashinPilotMoneyAttentionRequired\n"
                "FlashinPilotCapacityLow\n"
            )
        elif relative == "deploy/grafana/dashboards/flashin_operations.json":
            content = (
                '{"title":"FLASHIN Operations",'
                '"targets":["flashin_pilot_checkout_ready",'
                '"flashin_pilot_money_attention"]}\n'
            )
        elif relative == "deploy/grafana/provisioning/datasources/prometheus.yml":
            content = "type: prometheus\nurl: http://prometheus:9090\n"
        elif relative == "deploy/monitoring/prometheus.yml":
            content = (
                "rule_files:\n"
                "  - /etc/prometheus/rules/*.yml\n"
                "static_configs:\n"
                "  - targets: [backend:8000]\n"
            )
        elif relative == "scripts/check_production_compose.py":
            content = (
                'MONITORING_SERVICES = {"prometheus", "grafana"}\n'
                'PRODUCTION_PROFILES = ("production", "workers", "scheduler", "search", "monitoring")\n'
                'ERROR = "Grafana anonymous access must be disabled"\n'
            )
        elif relative == ".env.production.example":
            content = (
                "METRICS_ENABLED=true\n"
                "GRAFANA_ADMIN_USER=pilot-operator\n"
                "GRAFANA_ADMIN_PASSWORD=replace-me\n"
            )
        elif relative == "docker-compose.yml":
            content = (
                "services:\n"
                "  prometheus:\n"
                "    image: prom/prometheus:v3.5.0\n"
                "  grafana:\n"
                "    image: grafana/grafana:12.1.0\n"
                "volumes:\n"
                "  prometheus_data:\n"
                "  grafana_data:\n"
            )
        elif relative == ".github/workflows/ci.yml":
            content = (
                "jobs:\n"
                "  browser-e2e:\n"
                "    steps:\n"
                "      - name: Install Chromium\n"
                "      - name: Run Mini App and Admin browser journeys\n"
                "  docker:\n"
                "    needs: [backend, frontend, admin, browser-e2e]\n"
            )
        elif relative == "e2e/package.json":
            content = (
                "{\n"
                '  "name": "flashin-pilot-e2e",\n'
                '  "private": true,\n'
                '  "type": "module",\n'
                '  "scripts": {\n'
                '    "test": "playwright test"\n'
                "  },\n"
                '  "devDependencies": {\n'
                '    "@playwright/test": "1.54.2"\n'
                "  }\n"
                "}\n"
            )
        elif relative == "e2e/playwright.config.js":
            content = (
                'trace: "retain-on-failure"\n'
                'screenshot: "only-on-failure"\n'
                'video: "retain-on-failure"\n'
                'name: "storefront-mobile"\n'
                'name: "admin-desktop"\n'
            )
        elif relative == "e2e/tests/storefront.spec.js":
            content = (
                "Mini App critical pilot journey\n"
                "Mini App cart quantity and removal controls\n"
                "Mini App profile, support, privacy and return journey\n"
                "Mini App payment return route refreshes paid order\n"
            )
        elif relative == "e2e/tests/admin.spec.js":
            content = (
                "Admin critical pilot operator journey\n"
                "Admin operations, fulfillment and BusinessEvent recovery journey\n"
            )
        elif relative == "docs/pilot/end_to_end_coverage_matrix.md":
            content = (
                "## Browser journeys\n"
                "Six stateful Playwright journeys\n"
                "## Evidence boundary\n"
            )
        elif relative == "docker-compose.production.yml":
            content = "./docs:/app/docs:ro\n./deploy/release:/app/deploy/release:ro\n"
        elif relative == "scripts/deploy_production.sh":
            content = "pilot_runtime.py _stop\ncheck_pilot_runtime_integrity.py\n"
        elif relative == "scripts/rollback.sh":
            content = (
                'CAPABILITY_SCRIPT="scripts/pilot_release_capability.py"\n'
                'python3 "$CAPABILITY_SCRIPT" inspect --archive "$RELEASE"\n'
                "pilot_runtime.py _stop\n"
                "check_pilot_runtime_integrity.py\n"
            )
        path.write_text(content, encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "guarded release")
    return repo


def _release(repo: Path, tmp_path: Path, release_id: str, created_at: str) -> Path:
    state = create_release(
        repo,
        tmp_path / "builds",
        release_id=release_id,
        created_at=created_at,
    )
    return Path(state["archive"])


def test_signed_release_capability_is_bound_to_exact_release():
    assert CAPABILITY_VERSION == 3
    secret = "s" * 48
    state = _release_state()
    state["capabilities"] = {
        "pilot_runtime_guard": sign_payload(capability_payload(state), secret)
    }

    assert validate_capability(state, secret) == []

    state["sha256"] = "c" * 64
    errors = validate_capability(state, secret)
    assert any("archive_sha256" in error for error in errors)


def test_unsigned_or_tampered_release_capability_is_rejected():
    secret = "s" * 48
    state = _release_state()
    assert validate_capability(state, secret)

    capability = sign_payload(capability_payload(state), secret)
    capability["version"] = 99
    state["capabilities"] = {"pilot_runtime_guard": capability}
    errors = validate_capability(state, secret)
    assert any("signature" in error for error in errors)
    assert any("version" in error for error in errors)


def test_immutable_archive_inspection_accepts_guarded_release_and_rejects_missing_file(tmp_path):
    repo = _guarded_repo(tmp_path)
    guarded = _release(repo, tmp_path, "guarded", "2026-08-03T19:00:00Z")
    assert inspect_runtime_guard(guarded) == []

    missing_path = repo / "backend/services/pilot_circuit_breaker.py"
    missing_path.unlink()
    _git(repo, "add", "-u")
    _git(repo, "commit", "-qm", "remove payment circuit breaker")
    unguarded = _release(repo, tmp_path, "unguarded", "2026-08-03T19:01:00Z")
    errors = inspect_runtime_guard(unguarded)
    assert any("backend/services/pilot_circuit_breaker.py" in error for error in errors)


def test_immutable_archive_inspection_rejects_unwired_payment_breaker(tmp_path):
    repo = _guarded_repo(tmp_path)
    payments = repo / "backend/api/payments.py"
    payments.write_text("class ProviderPaymentIntegrityError: pass\n", encoding="utf-8")
    _git(repo, "add", str(payments.relative_to(repo)))
    _git(repo, "commit", "-qm", "remove payment breaker wiring")

    release = _release(repo, tmp_path, "unwired", "2026-08-03T19:02:00Z")
    errors = inspect_runtime_guard(release)
    assert any("backend/api/payments.py" in error for error in errors)
    assert any("trip_pilot_circuit_breaker" in error for error in errors)


def test_immutable_archive_inspection_rejects_unwired_browser_gate(tmp_path):
    repo = _guarded_repo(tmp_path)
    workflow = repo / ".github/workflows/ci.yml"
    workflow.write_text(
        "jobs:\n  docker:\n    needs: [backend, frontend, admin]\n",
        encoding="utf-8",
    )
    _git(repo, "add", str(workflow.relative_to(repo)))
    _git(repo, "commit", "-qm", "remove browser gate wiring")

    release = _release(repo, tmp_path, "no-browser-gate", "2026-08-03T19:03:00Z")
    errors = inspect_runtime_guard(release)
    assert any(".github/workflows/ci.yml" in error for error in errors)
    assert any("browser-e2e" in error for error in errors)


def test_immutable_archive_inspection_rejects_unwired_pilot_metrics(tmp_path):
    repo = _guarded_repo(tmp_path)
    metrics = repo / "backend/middleware/metrics.py"
    metrics.write_text("def metrics_response(): pass\n", encoding="utf-8")
    _git(repo, "add", str(metrics.relative_to(repo)))
    _git(repo, "commit", "-qm", "remove pilot metrics wiring")

    release = _release(repo, tmp_path, "no-pilot-metrics", "2026-08-03T19:04:00Z")
    errors = inspect_runtime_guard(release)
    assert any("backend/middleware/metrics.py" in error for error in errors)
    assert any("flashin_pilot_metrics_collection_success" in error for error in errors)
