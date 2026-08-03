from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LAUNCH = ROOT / "scripts" / "launch.py"
DEPLOY = ROOT / "scripts" / "deploy_production.sh"
COMPOSE = ROOT / "docker-compose.yml"


def test_production_launcher_delegates_to_hardened_deploy_script():
    source = LAUNCH.read_text(encoding="utf-8")

    assert 'run(["bash", "scripts/deploy_production.sh"])' in source
    assert "--skip-build is not allowed in production mode" in source
    assert "docker compose up -d backend frontend admin bot" not in source


def test_launcher_validates_non_empty_required_values():
    source = LAUNCH.read_text(encoding="utf-8")

    assert "if not values.get(key)" in source
    assert '"ADMIN_TOTP_ENCRYPTION_KEY"' in source


def test_production_deploy_requires_gate_and_migration_aware_readiness():
    deploy = DEPLOY.read_text(encoding="utf-8")
    compose = COMPOSE.read_text(encoding="utf-8")

    assert "python3 scripts/readiness_gate.py --phase predeploy" in deploy
    assert "http://localhost:8000/ready" in deploy
    assert "http://localhost:8000/ready" in compose
    assert "Run 'make pilot-gate'" in deploy
