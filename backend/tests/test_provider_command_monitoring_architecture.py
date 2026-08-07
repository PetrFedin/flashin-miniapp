import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
METRICS = ROOT / "backend" / "middleware" / "metrics.py"
MAIN = ROOT / "backend" / "main.py"
RULES = ROOT / "deploy" / "monitoring" / "rules" / "flashin_pilot.yml"
DASHBOARD = ROOT / "deploy" / "grafana" / "dashboards" / "flashin_provider_commands.json"


def test_metrics_use_only_bounded_provider_command_labels():
    source = METRICS.read_text(encoding="utf-8")

    assert '"flashin_provider_command_metrics_collection_success"' in source
    assert '"flashin_provider_commands"' in source
    assert '["provider", "status"]' in source
    assert '"flashin_provider_command_oldest_age_seconds"' in source
    assert '"flashin_provider_command_actionable"' in source
    assert '"flashin_provider_command_oldest_actionable_age_seconds"' in source
    assert '"flashin_provider_command_due"' in source
    assert '["provider", "kind"]' in source
    for forbidden_label in ("order_id", "refund_id", "command_id", "external_id"):
        assert f'["{forbidden_label}"]' not in source
        assert f', "{forbidden_label}"' not in source


def test_metrics_endpoint_collects_provider_queue_before_rendering():
    source = MAIN.read_text(encoding="utf-8")

    assert "collect_provider_command_metrics" in source
    metrics_route = source[source.index('@app.get("/metrics"') : source.index('@app.get("/"')]
    assert metrics_route.index("collect_pilot_metrics") < metrics_route.index(
        "collect_provider_command_metrics"
    ) < metrics_route.index("metrics_response")


def test_provider_command_alerts_cover_telemetry_review_failure_lease_and_backlog():
    source = RULES.read_text(encoding="utf-8")

    required = (
        "FlashinProviderCommandMetricsUnavailable",
        "FlashinProviderCommandReviewRequired",
        "FlashinProviderCommandFailed",
        "FlashinProviderCommandLeaseExpired",
        "FlashinProviderCommandBacklogStale",
        'flashin_provider_commands{provider="moysklad",status="review_required"} > 0',
        'flashin_provider_commands{provider="moysklad",status="failed"} > 0',
        'flashin_provider_command_due{provider="moysklad",kind="expired_processing"} > 0',
        'flashin_provider_command_oldest_actionable_age_seconds{provider="moysklad"} > 300',
        "component: provider-integration",
    )
    for fragment in required:
        assert fragment in source


def test_provider_command_dashboard_is_valid_and_uses_bounded_queries():
    dashboard = json.loads(DASHBOARD.read_text(encoding="utf-8"))

    assert dashboard["uid"] == "flashin-provider-commands"
    assert dashboard["title"] == "FLASHIN Provider Commands"
    assert len(dashboard["panels"]) >= 6
    rendered = json.dumps(dashboard, sort_keys=True)
    for fragment in (
        'flashin_provider_commands{provider=\\"moysklad\\",status=\\"review_required\\"}',
        'flashin_provider_commands{provider=\\"moysklad\\",status=\\"failed\\"}',
        'flashin_provider_command_due{provider=\\"moysklad\\",kind=\\"expired_processing\\"}',
        'flashin_provider_command_oldest_actionable_age_seconds{provider=\\"moysklad\\"}',
    ):
        assert fragment in rendered
    for forbidden in ("order_id", "refund_id", "command_id", "external_id"):
        assert forbidden not in rendered
