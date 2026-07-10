from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

def test_worker_scripts_exist():
    for name in [
        "run_ops_jobs.py",
        "run_outbox_jobs.py",
        "run_moysklad_sync.py",
        "run_campaign_jobs.py",
        "run_sla_jobs.py",
    ]:
        assert (ROOT / "scripts" / name).exists()

def test_dockerfiles_copy_required_modules():
    backend = (ROOT / "Dockerfile.backend").read_text()
    bot = (ROOT / "Dockerfile.bot").read_text()
    assert "COPY scripts" in backend
    assert "COPY backend" in bot
