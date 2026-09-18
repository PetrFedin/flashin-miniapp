#!/usr/bin/env python3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

REQUIRED_FILES = [
    "Dockerfile.backend",
    "Dockerfile.bot",
    "Dockerfile.frontend",
    "Dockerfile.admin",
    "docker-compose.yml",
    "docker-compose.production.yml",
    "Makefile",
    ".env.local.example",
    ".env.production.example",
    "backend/main.py",
    "backend/models.py",
    "backend/model_constraints.py",
    "backend/notification_models.py",
    "backend/alembic/env.py",
    "backend/alembic/versions/0001_initial_production.py",
    "backend/alembic/versions/0008_platform_cms_events_media_scheduler.py",
    "backend/alembic/versions/0009_security_payment_delivery_media_hardening.py",
    "backend/alembic/versions/0010_transaction_integrity_constraints.py",
    "backend/alembic/versions/0011_refund_integrity_and_loyalty_reversals.py",
    "backend/alembic/versions/0012_notification_delivery_retry_state.py",
    "backend/alembic/versions/0013_webhook_outbox_integrity.py",
    "backend/services/refund_loyalty.py",
    "backend/services/refund_state.py",
    "backend/services/notification_delivery.py",
    "backend/services/webhook_security.py",
    "backend/api/admin_notifications.py",
    "backend/jobs/refund_jobs.py",
    "backend/tests/test_transaction_integrity_metadata.py",
    "backend/tests/test_integrity_audit_versioning.py",
    "backend/tests/test_notification_delivery.py",
    "backend/tests/test_validate_env.py",
    "backend/tests/test_scheduler_moysklad.py",
    "backend/tests/test_moysklad_integrity.py",
    "backend/tests/test_webhook_security.py",
    "backend/tests/test_production_compose_gate.py",
    "frontend/package.json",
    "admin/package.json",
    "bot/main.py",
    "bot/send_notifications.py",
    "scripts/bootstrap.sh",
    "scripts/migrate.sh",
    "scripts/healthcheck.sh",
    "scripts/check_transaction_integrity.py",
    "scripts/check_production_compose.py",
    "scripts/container_smoke.py",
    "scripts/deploy_production.sh",
    "deploy/k8s/backend-deployment.yaml",
    "backend/services/media_pipeline.py",
    "backend/services/event_dispatcher.py",
    "backend/api/import_export.py",
    "backend/api/platform.py",
    ".github/workflows/ci.yml",
    "scripts/install.sh",
    "backend/tests/test_v42_release_ops.py",
    "deploy/release/release_manifest.template.json",
    "deploy/secrets/infisical.template.env",
    "backend/api/v1/router.py",
    "backend/tests/test_v43_diagnostics.py",
    "docs/runbook_index.md",
    "docs/developer_handover.md",
    "deploy/statuspage/index.html",
    "scripts/generate_release_notes.py",
    "scripts/generate_openapi_snapshot.py",
    "scripts/validate_env.py",
    "backend/tests/test_v44_launch_files.py",
    "docs/v44_what_to_fill_before_launch.md",
    "docs/v44_launch_cockpit.md",
    "backend/tests/test_v45_final_docs.py",
    "docs/sop/post_launch_metrics_plan.md",
    "docs/sop/data_retention_policy.md",
    "docs/sop/admin_onboarding.md",
    "docs/sop/support_sop.md",
    "docs/incident_templates/payment_incident.md",
    "docs/v45_final_acceptance.md",
    "backend/tests/test_v46_post_launch_files.py",
    "docs/templates/bug_report_template.md",
    "docs/post_launch/support_handover_pack.md",
    "backend/tests/test_v47_hardening_files.py",
    "deploy/loadtest/k6_webhook_burst.js",
    "deploy/loadtest/k6_catalog_search_checkout.js",
    "deploy/grafana/dashboards/flashin_operations.json",
    "scripts/security_audit.sh",
    "scripts/run_media_jobs.py",
    "backend/jobs/media_jobs.py",
    "backend/services/admin_security.py",
    "backend/api/moysklad_deep_mapping.py",
    "backend/api/delivery_providers.py",
    "backend/api/payment_reconciliation.py",
    "docs/post_launch/roadmap_backlog.md",
    "docs/post_launch/kpi_dashboard_spec.md",
    "docs/post_launch/day_30_scale_plan.md",
    "docs/post_launch/day_7_review.md",
    "docs/post_launch/day_0_checklist.md",
    "scripts/performance_budget.py",
    "deploy/loadtest/k6_smoke.js",
    "docs/v45_master_launch_checklist.md",
    "docs/v45_launch_command_center.md",
    "scripts/readiness_gate.py",
    "scripts/generate_20_order_pilot_sheet.py",
    "scripts/production_readiness_report.py",
    "scripts/check_integrations.py",
    "scripts/setup_wizard.py",
    "backend/services/diagnostics.py",
    "backend/api/diagnostics.py",
    "scripts/seed_admin.py",
    "scripts/verify_backup.sh",
    "scripts/rollback.sh",
    "scripts/run_ops_jobs.py",
    "scripts/run_outbox_jobs.py",
    "scripts/run_moysklad_sync.py",
    "scripts/run_campaign_jobs.py",
    "scripts/run_sla_jobs.py",
    "frontend/public/legal/offer.html",
    "frontend/public/legal/privacy.html",
    "frontend/public/legal/returns.html",
]

BASE_REQUIRED_ENV_KEYS = (
    "DATABASE_URL",
    "TELEGRAM_BOT_TOKEN",
    "JWT_SECRET",
    "ADMIN_EMAIL",
    "MINI_APP_URL",
    "API_PUBLIC_URL",
)


def load_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def required_env_keys(env: dict[str, str]) -> tuple[str, ...]:
    app_env = env.get("APP_ENV", "development").strip().lower()
    if app_env == "production":
        return BASE_REQUIRED_ENV_KEYS
    return BASE_REQUIRED_ENV_KEYS + ("ADMIN_PASSWORD",)


def validate_admin_password_contract(env: dict[str, str]) -> list[str]:
    app_env = env.get("APP_ENV", "development").strip().lower()
    errors: list[str] = []
    missing = [key for key in required_env_keys(env) if key not in env]
    if missing:
        errors.append("Missing .env keys: " + ", ".join(missing))
    if app_env == "production" and env.get("ADMIN_PASSWORD", "").strip():
        errors.append(
            "ADMIN_PASSWORD must not be stored in production; "
            "use the interactive first-admin bootstrap"
        )
    return errors


def run_preflight(root: Path = ROOT) -> list[str]:
    errors: list[str] = []
    missing_files = [path for path in REQUIRED_FILES if not (root / path).exists()]
    if missing_files:
        errors.append("Missing required files: " + ", ".join(missing_files))

    env_path = root / ".env"
    if env_path.exists():
        errors.extend(validate_admin_password_contract(load_env(env_path)))
    return errors


def main() -> int:
    errors = run_preflight()
    if errors:
        for error in errors:
            print(error)
        return 1
    print("Preflight OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
