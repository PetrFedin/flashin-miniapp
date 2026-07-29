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
    "Makefile",
    ".env.local.example",
    ".env.production.example",
    "backend/main.py",
    "backend/models.py",
    "backend/model_constraints.py",
    "backend/alembic/versions/0001_initial_production.py",
    "backend/alembic/versions/0008_platform_cms_events_media_scheduler.py",
    "backend/alembic/versions/0009_security_payment_delivery_media_hardening.py",
    "backend/alembic/versions/0010_transaction_integrity_constraints.py",
    "backend/alembic/versions/0011_refund_integrity_and_loyalty_reversals.py",
    "backend/services/refund_loyalty.py",
    "backend/tests/test_transaction_integrity_metadata.py",
    "frontend/package.json",
    "admin/package.json",
    "bot/main.py",
    "scripts/bootstrap.sh",
    "scripts/migrate.sh",
    "scripts/healthcheck.sh",
    "scripts/check_transaction_integrity.py",
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

missing = [path for path in REQUIRED_FILES if not (ROOT / path).exists()]
if missing:
    print("Missing required files:")
    for path in missing:
        print(" -", path)
    sys.exit(1)

env_path = ROOT / ".env"
if env_path.exists():
    env = env_path.read_text()
    required_keys = [
        "DATABASE_URL",
        "TELEGRAM_BOT_TOKEN",
        "JWT_SECRET",
        "ADMIN_EMAIL",
        "ADMIN_PASSWORD",
        "MINI_APP_URL",
        "API_PUBLIC_URL",
    ]
    missing_keys = [key for key in required_keys if f"{key}=" not in env]
    if missing_keys:
        print("Missing .env keys:", missing_keys)
        sys.exit(1)

print("Preflight OK")
