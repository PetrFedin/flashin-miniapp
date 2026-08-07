from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REQUIRED_CHECKS = "backend,frontend,admin,browser-e2e,integrated-e2e,docker"


def _text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_integrated_e2e_is_a_required_governance_check():
    production_env = _text(".env.production.example")
    workflow = _text(".github/workflows/ci.yml")

    assert f"PILOT_GITHUB_REQUIRED_CHECKS={REQUIRED_CHECKS}" in production_env
    assert "  integrated-e2e:" in workflow
    assert "Run real internal-stack browser journey" in workflow
    assert "needs: [backend, frontend, admin, browser-e2e, integrated-e2e]" in workflow


def test_integrated_e2e_runs_real_flashin_api_and_database_without_page_api_mocks():
    spec = _text("e2e/integrated/pilot-stack.spec.js")
    config = _text("e2e/playwright.integrated.config.js")
    wrapper = _text("scripts/integrated_e2e_app.py")

    assert "page.route(" not in spec
    assert "/api/orders/checkout" in spec
    assert "/api/payments" in spec
    assert "Fulfillment & Delivery" in spec
    assert "scripts.integrated_e2e_app:app" in config
    assert 'url: `${apiBase}/ready`' in config
    assert "payment_service._YOOKASSA_API = _PROVIDER_BASE" in wrapper
    assert "from backend.main import app" in wrapper


def test_test_only_provider_boundary_is_fail_closed_and_not_wired_to_production():
    wrapper = _text("scripts/integrated_e2e_app.py")
    compose = _text("docker-compose.yml") + "\n" + _text("docker-compose.production.yml")

    assert 'not in {"test", "ci"}' in wrapper
    assert 'os.getenv("INTEGRATED_E2E"' in wrapper
    assert "Integrated E2E app is forbidden outside test/ci" in wrapper
    assert "integrated_e2e_app" not in compose


def test_integrated_job_explicitly_disables_live_pilot_and_external_search_boundaries():
    workflow = _text(".github/workflows/ci.yml")

    assert 'PILOT_RUNTIME_ENFORCED: "false"' in workflow
    assert 'MEILISEARCH_ENABLED: "false"' in workflow
    assert 'INTEGRATED_E2E: "true"' in workflow
    assert "YOOKASSA_SHOP_ID: e2e-shop" in workflow
