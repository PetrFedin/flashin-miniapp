from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LAUNCH = ROOT / "scripts" / "launch.py"


def test_production_launcher_delegates_to_hardened_deploy_script():
    source = LAUNCH.read_text(encoding="utf-8")

    assert 'run(["bash", "scripts/deploy_production.sh"])' in source
    assert "--skip-build is not allowed in production mode" in source
    assert "docker compose up -d backend frontend admin bot" not in source


def test_launcher_validates_non_empty_required_values():
    source = LAUNCH.read_text(encoding="utf-8")

    assert "if not values.get(key)" in source
    assert '"ADMIN_TOTP_ENCRYPTION_KEY"' in source
