from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DEPLOY = ROOT / "scripts" / "deploy_production.sh"


def test_alert_delivery_smoke_is_isolated_from_existing_prometheus_traffic():
    source = DEPLOY.read_text(encoding="utf-8")

    stop_prometheus = source.index("docker compose stop prometheus")
    fresh_alertmanager = source.index("docker compose up -d --force-recreate alertmanager")
    delivery_smoke = source.index("python scripts/alertmanager_delivery_smoke.py")
    start_monitoring = source.index("notification_worker scheduler meilisearch alertmanager prometheus grafana")
    promote_release = source.index("python3 scripts/release_control.py promote")

    assert stop_prometheus < fresh_alertmanager < delivery_smoke < start_monitoring < promote_release
    assert "ps --status running --services" in source
    assert "Proving isolated external alert delivery before release promotion" in source
