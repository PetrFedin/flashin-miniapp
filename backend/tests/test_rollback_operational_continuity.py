from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DEPLOY = ROOT / "scripts" / "deploy_production.sh"
ROLLBACK = ROOT / "scripts" / "rollback.sh"
ROLLBACK_SMOKE = ROOT / "scripts" / "release_rollback_smoke.sh"
PROVIDER_WORKER = ROOT / "scripts" / "run_provider_command_jobs.py"


def test_production_deploy_supervises_durable_provider_dispatch():
    source = DEPLOY.read_text(encoding="utf-8")
    worker = PROVIDER_WORKER.read_text(encoding="utf-8")

    assert "while True:" in worker
    assert "PROVIDER_COMMAND_POLL_SECONDS" in worker
    assert "notification_worker provider_command_jobs scheduler meilisearch" in source
    assert "Dedicated provider-command polling and scheduler fallback are both running." in source


def test_rollback_preserves_operator_alerting_secrets_and_runtime_state():
    source = ROLLBACK.read_text(encoding="utf-8")

    assert "production,workers,scheduler,search,monitoring" in source
    assert "--exclude 'deploy/runtime/'" in source
    assert "--exclude 'deploy/secrets/alertmanager.env'" in source
    assert 'ALERTMANAGER_SECRET_ENV="deploy/secrets/alertmanager.env"' in source
    assert 'python3 "$ALERTMANAGER_RENDERER"' in source
    assert "Production rollback requires preserved $ALERTMANAGER_SECRET_ENV" in source


def test_rollback_fails_before_downtime_for_release_without_operational_plane():
    source = ROLLBACK.read_text(encoding="utf-8")

    archive_check = source.index('python3 - "$RELEASE"')
    downtime = source.index("docker compose down")
    assert archive_check < downtime
    for required in (
        "scripts/render_alertmanager_config.py",
        "scripts/run_provider_command_jobs.py",
        "deploy/monitoring/prometheus.yml",
        "deploy/monitoring/rules/flashin_pilot.yml",
        "deploy/grafana/provisioning/datasources/prometheus.yml",
    ):
        assert required in source


def test_rollback_rebuilds_and_restores_provider_worker_and_monitoring():
    source = ROLLBACK.read_text(encoding="utf-8")

    assert "backend frontend admin bot notification_worker provider_command_jobs scheduler" in source
    assert "notification_worker provider_command_jobs scheduler meilisearch" in source
    assert "alertmanager prometheus grafana" in source
    assert "http://alertmanager:9093/-/ready" in source
    assert "http://prometheus:9090/api/v1/alertmanagers" in source
    assert "FlashinPilotRuntimeStopped" in source
    assert "http://grafana:3000/api/health" in source
    assert "Provider-command worker and internal monitoring were restored and verified." in source


def test_signed_full_rollback_drill_covers_operational_continuity():
    source = ROLLBACK_SMOKE.read_text(encoding="utf-8")

    assert "production,workers,scheduler,search,monitoring" in source
    assert "notification_worker provider_command_jobs scheduler meilisearch" in source
    assert "alertmanager prometheus grafana" in source
    assert "for service in provider_command_jobs alertmanager prometheus grafana" in source
    assert "provider_command_worker_restored" in source
    assert "monitoring_restored" in source
