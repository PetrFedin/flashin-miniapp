from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

def test_release_scripts_exist():
    for path in [
        "scripts/deploy_production.sh",
        "scripts/rollback.sh",
        "scripts/verify_backup.sh",
        "scripts/seed_admin.py",
        "deploy/release/release_manifest.template.json",
        "deploy/secrets/infisical.template.env",
    ]:
        assert (ROOT / path).exists()

def test_api_v1_scaffold_exists():
    assert (ROOT / "backend/api/v1/router.py").exists()
