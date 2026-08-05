import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from pilot_evidence import sign_payload  # noqa: E402
from pilot_release_capability import (  # noqa: E402
    CAPABILITY_VERSION,
    REQUIRED_FILES,
    capability_payload,
    inspect_runtime_guard,
    validate_capability,
)
from release_control import create_release  # noqa: E402


def _release_state():
    return {
        "release_id": "release-guarded",
        "git_commit": "a" * 40,
        "sha256": "b" * 64,
    }


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
    )


FILE_CONTENT = {
    "backend/api/orders.py": "acquire_pilot_checkout()\nrecord_pilot_order()\n",
    "backend/services/pilot_circuit_breaker.py": (
        "def stop_pilot_for_order():\n    pass\n"
        "def trip_pilot_circuit_breaker():\n    pass\n"
    ),
    "backend/api/payments.py": (
        "class ProviderPaymentIntegrityError: pass\n"
        "trip_pilot_circuit_breaker()\nstop_pilot_for_order()\n"
    ),
    "backend/api/returns.py": "trip_pilot_circuit_breaker()\nstop_pilot_for_order()\n",
    "backend/services/payment_reconciliation.py": (
        "payment_reconciliation_mismatch\nstop_pilot_for_order()\n"
    ),
    "backend/order_statuses.py": (
        "SETTLED_ORDER_PAYMENT_STATUSES = frozenset({\n"
        '    "paid", "paid_review_required", "refund_review_required"\n'
        "})\n"
    ),
    "backend/services/payment_settlement.py": (
        "from ..order_statuses import SETTLED_ORDER_PAYMENT_STATUSES\n"
        "if order.payment_status in SETTLED_ORDER_PAYMENT_STATUSES:\n    return False\n"
        "reward_referral_after_first_paid_order(db, order.customer_id, order.id)\n"
    ),
    "backend/services/loyalty.py": (
        "def _lock_referral_customer():\n    pass\n"
        "def _has_prior_settled_order():\n    pass\n"
        "Referral code must be applied before the first paid order\n"
        "return attach_referral_to_customer(db, code, new_customer_id)\n"
        'attribution.status = "ineligible"\n'
        "def reward_referral_after_first_paid_order():\n    pass\n"
    ),
    "backend/tests/test_referral_attribution.py": (
        "test_legacy_apply_referral_only_attaches_pending_attribution\n"
        "test_referral_after_settled_order_is_rejected_even_for_same_code\n"
        "test_missing_customer_is_not_silently_eligible\n"
    ),
    "backend/services/fulfillment.py": (
        "def _picklist_is_complete():\n    pass\n"
        "Every picklist item must be fully picked before packing\n"
        'order.delivery_status = "ready"\n'
    ),
    "backend/api/fulfillment.py": (
        "fulfillment.task.update\nfulfillment.task_item.update\nassigned_admin_id\n"
    ),
    "backend/services/delivery_providers.py": (
        "_SHIPMENT_TRANSITIONS = {}\n"
        "Only a ready order can be transferred to delivery\n"
        'order.status = "shipped"\norder.status = "completed"\n'
    ),
    "backend/api/delivery_providers.py": (
        "delivery.shipment.create\ndelivery.shipment.update\nwith_for_update()\n"
    ),
    "backend/main.py": (
        "collect_pilot_metrics\n"
        '@app.get("/metrics"\n'
        "return metrics_response()\n"
    ),
    "backend/middleware/metrics.py": (
        "flashin_pilot_metrics_collection_success\n"
        "def collect_pilot_metrics():\n    pass\n"
        'return "__unmatched__"\n'
    ),
    "deploy/monitoring/rules/flashin_pilot.yml": (
        "FlashinPilotMetricsUnavailable\n"
        "FlashinPilotArtifactIntegrityFailed\n"
        "FlashinPilotMoneyAttentionRequired\n"
        "FlashinPilotCapacityLow\n"
    ),
    "deploy/grafana/dashboards/flashin_operations.json": (
        "FLASHIN Operations\nflashin_pilot_checkout_ready\nflashin_pilot_money_attention\n"
    ),
    "deploy/grafana/provisioning/datasources/prometheus.yml": (
        "prometheus\nhttp://prometheus:9090\n"
    ),
    "deploy/monitoring/prometheus.yml": (
        "rule_files\n/etc/prometheus/rules/*.yml\nbackend:8000\n"
    ),
    "scripts/check_production_compose.py": (
        'MONITORING_SERVICES = {"prometheus", "grafana"}\n'
        'PRODUCTION_PROFILES = ("production", "workers", "scheduler", "search", "monitoring")\n'
        "Grafana anonymous access must be disabled\n"
    ),
    ".env.production.example": (
        "METRICS_ENABLED=true\nGRAFANA_ADMIN_USER=pilot\nGRAFANA_ADMIN_PASSWORD=secret\n"
    ),
    "docker-compose.yml": (
        "prometheus:\ngrafana:\nprometheus_data\ngrafana_data\n"
    ),
    ".github/workflows/ci.yml": (
        "browser-e2e:\nInstall Chromium\nRun Mini App and Admin browser journeys\n"
        "Run transactional referral attribution smoke\n"
        "Run transactional full fulfillment smoke\n"
        "Run signed backup and restore drill\n"
        "bash scripts/backup_restore_smoke.sh\n"
        "needs: [backend, frontend, admin, browser-e2e]\n"
    ),
    "e2e/package.json": (
        "{\n"
        '  "scripts": {"test": "playwright test"},\n'
        '  "devDependencies": {"@playwright/test": "1.54.2"}\n'
        "}\n"
    ),
    "e2e/playwright.config.js": (
        'name: "storefront-mobile"\nname: "admin-desktop"\n'
        'trace: "retain-on-failure"\nscreenshot: "only-on-failure"\n'
        'video: "retain-on-failure"\n'
    ),
    "e2e/tests/storefront.spec.js": (
        "Mini App critical pilot journey\n"
        "Mini App cart quantity and removal controls\n"
        "Mini App profile, support, privacy and return journey\n"
        "Mini App payment return route refreshes paid order\n"
    ),
    "e2e/tests/admin.spec.js": (
        "Admin critical pilot operator journey\n"
        "Admin operations, fulfillment and BusinessEvent recovery journey\n"
        "Admin completes support, privacy and refund service operations\n"
    ),
    "e2e/tests/owner-admin.spec.js": (
        "Admin assigns an accountable owner to a support ticket\n"
        "assigned_admin_id: 42\nОтветственный обращения 901\n"
    ),
    "e2e/tests/fulfillment-admin.spec.js": (
        "Admin completes picklist, shipment and delivery lifecycle\n"
        "Собрать все позиции и упаковать\nPILOT-TRACK-9100\n"
        'status: "completed"\n'
    ),
    "backend/api/support.py": (
        "class AdminSupportTicketOut:\n    assigned_admin_id: int | None = None\n"
        "response_model=list[AdminSupportTicketOut]\n"
        "response_model=AdminSupportTicketOut\n"
    ),
    "backend/tests/test_support_admin_schema.py": (
        "test_admin_support_ticket_schema_exposes_accountable_owner\nassigned_admin_id\n"
    ),
    "admin/src/FulfillmentOperationsPanel.jsx": (
        '"/api/fulfillment/tasks"\n"/api/delivery-providers/shipments"\n'
        "async function pickAndPack() {}\nasync function ship() {}\n"
        "async function deliver() {}\n"
    ),
    "admin/src/fulfillmentOperations.js": (
        "export function isPicklistComplete() {}\n"
        "export function fulfillmentAction() {}\n"
        "export function normalizeTracking() {}\n"
        "export function fulfillmentAttentionCount() {}\n"
        "Собрать все позиции и упаковать\nПередать в доставку\nПодтвердить доставку\n"
    ),
    "admin/src/fulfillmentOperations.test.js": (
        "fulfillment actions expose only the next safe workflow step\n"
        "picklist completeness requires every ordered unit\n"
        "tracking is bounded and meaningful\n"
        "attention remains until shipment is delivered\n"
    ),
    "admin/src/ServiceOperationsPanel.jsx": (
        'support: "/api/support/admin/tickets"\n'
        'privacy: "/api/privacy/admin/requests"\n'
        'returns: "/api/admin/returns"\n'
        'adminJson("/api/returns/admin/approve"\n'
        "Подтвердить refund\nОтветственный обращения\n"
    ),
    "admin/src/serviceOperations.js": (
        "export function supportTransitions() {}\n"
        "export function canProcessPrivacy() {}\n"
        "export function canApproveReturn() {}\n"
        "export function normalizeAdminAssignment() {}\n"
        "export function normalizeRefundAmount() {}\n"
        "export function serviceAttentionCount() {}\n"
    ),
    "admin/src/serviceOperations.test.js": (
        "support transitions follow the backend state machine\n"
        "support owner assignment accepts only positive integer Admin IDs\n"
        "refund amount is positive, bounded and rounded\n"
        "aggregate attention are fail-closed\n"
    ),
    "admin/src/BusinessEventsPanel.jsx": (
        'import FulfillmentOperationsPanel from "./FulfillmentOperationsPanel.jsx"\n'
        "<FulfillmentOperationsPanel onUnauthorized={onUnauthorized} />\n"
        'import ServiceOperationsPanel from "./ServiceOperationsPanel.jsx"\n'
        "<ServiceOperationsPanel onUnauthorized={onUnauthorized} />\n"
    ),
    "admin/index.html": (
        'href="/src/serviceOperations.css"\nFLASHIN Admin\n'
    ),
    "admin/src/serviceOperations.css": (
        ".service-operations {}\n.service-grid {}\n.attention-badge {}\n"
    ),
    "scripts/full_fulfillment_smoke.py": (
        "Every picklist item must be fully picked before packing\n"
        "idempotent shipment create\n"
        'persisted_order.status == "completed"\n'
        'persisted_order.delivery_status == "delivered"\n'
    ),
    "scripts/referral_attribution_smoke.py": (
        "duplicate referral payment webhook\nlate_referral.status_code == 409\n"
        "persisted_referral.used_count == 1\nlen(reward_rows) == 1\n"
        "second_persisted_order.referral_code is None\n"
    ),
    "scripts/backup_integrity.py": (
        'KIND = "postgres_backup_manifest"\nCRITICAL_TABLES = (\n'
        "def snapshot_database():\n    pass\n"
        "def verify_restorable():\n    pass\n"
        "def verify_live_database():\n    pass\n"
        "backup SHA-256 does not match signed manifest\n"
        "restored critical table\n"
    ),
    "scripts/backup_postgres.sh": (
        'MANIFEST_FILE=x\npython3 "$INTEGRITY_SCRIPT" create\n'
        "Backup created, restored in isolation and signed\n"
    ),
    "scripts/verify_backup.sh": (
        'Signed backup manifest not found\npython3 "$INTEGRITY_SCRIPT" verify\n'
        "Backup signature, archive, schema and critical data verification OK\n"
    ),
    "scripts/restore_postgres.sh": (
        'Signed backup manifest not found\npython3 "$INTEGRITY_SCRIPT" verify\n'
        'python3 "$INTEGRITY_SCRIPT" verify-live\nsigned snapshot verified\n'
    ),
    "scripts/backup_restore_smoke.sh": (
        "tampered_archive_rejected\nmutated_database_rejected\n"
        "restored_value_verified\nverify-live\nrestore_postgres.sh --yes\n"
    ),
    "backend/tests/test_backup_integrity.py": (
        "test_signed_manifest_binds_exact_archive_and_snapshot\n"
        "test_archive_byte_or_size_change_is_rejected\n"
        "test_snapshot_comparison_detects_schema_revision_and_ledger_changes\n"
        "test_database_identifiers_fail_closed\n"
    ),
    "docs/pilot/end_to_end_coverage_matrix.md": (
        "## Browser journeys\nNine stateful Playwright journeys\n"
        "accountable active Admin ID\nAdmin service operations\nfull picklist\n"
        "## Transactional referral evidence\nfirst paid order -> one inviter reward\n"
        "## Signed backup and restore evidence\nBackup/restore integrity\n"
        "Release rollback\n## Evidence boundary\n"
    ),
    "docker-compose.production.yml": (
        "./docs:/app/docs:ro\n./deploy/release:/app/deploy/release:ro\n"
    ),
    "scripts/deploy_production.sh": (
        "pilot_runtime.py _stop\ncheck_pilot_runtime_integrity.py\n"
    ),
    "scripts/rollback.sh": (
        'CAPABILITY_SCRIPT="scripts/pilot_release_capability.py"\n'
        '"$CAPABILITY_SCRIPT" inspect --archive\n'
        "scripts/verify_backup.sh\nrestore_postgres.sh\n"
        "pilot_runtime.py _stop\ncheck_pilot_runtime_integrity.py\n"
    ),
}


def _guarded_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "pilot@example.com")
    _git(repo, "config", "user.name", "Pilot Test")
    for relative in REQUIRED_FILES:
        path = repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(FILE_CONTENT.get(relative, "guarded\n"), encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "guarded release")
    return repo


def _release(repo: Path, tmp_path: Path, release_id: str, created_at: str) -> Path:
    state = create_release(
        repo,
        tmp_path / "builds",
        release_id=release_id,
        created_at=created_at,
    )
    return Path(state["archive"])


def test_signed_release_capability_is_bound_to_exact_release():
    assert CAPABILITY_VERSION == 7
    secret = "s" * 48
    state = _release_state()
    state["capabilities"] = {
        "pilot_runtime_guard": sign_payload(capability_payload(state), secret)
    }

    assert validate_capability(state, secret) == []

    state["sha256"] = "c" * 64
    errors = validate_capability(state, secret)
    assert any("archive_sha256" in error for error in errors)


def test_unsigned_or_tampered_release_capability_is_rejected():
    secret = "s" * 48
    state = _release_state()
    assert validate_capability(state, secret)

    capability = sign_payload(capability_payload(state), secret)
    capability["version"] = 99
    state["capabilities"] = {"pilot_runtime_guard": capability}
    errors = validate_capability(state, secret)
    assert any("signature" in error for error in errors)
    assert any("version" in error for error in errors)


def test_immutable_archive_accepts_complete_capability_and_rejects_missing_file(tmp_path):
    repo = _guarded_repo(tmp_path)
    guarded = _release(repo, tmp_path, "guarded", "2026-08-05T00:00:00Z")
    assert inspect_runtime_guard(guarded) == []

    missing_path = repo / "scripts/backup_integrity.py"
    missing_path.unlink()
    _git(repo, "add", "-u")
    _git(repo, "commit", "-qm", "remove backup integrity")
    unguarded = _release(repo, tmp_path, "unguarded", "2026-08-05T00:01:00Z")
    errors = inspect_runtime_guard(unguarded)
    assert any("scripts/backup_integrity.py" in error for error in errors)


@pytest.mark.parametrize(
    ("path", "replacement", "expected_marker"),
    [
        ("backend/api/payments.py", "class ProviderPaymentIntegrityError: pass\n", "trip_pilot_circuit_breaker"),
        (".github/workflows/ci.yml", "jobs:\n  docker:\n    needs: [backend]\n", "browser-e2e"),
        ("backend/middleware/metrics.py", "def metrics_response(): pass\n", "flashin_pilot_metrics_collection_success"),
        ("admin/src/BusinessEventsPanel.jsx", "export default function Panel() {}\n", "ServiceOperationsPanel"),
        ("backend/api/support.py", "class AdminSupportTicketOut: pass\n", "assigned_admin_id"),
        ("admin/src/FulfillmentOperationsPanel.jsx", "export default function Panel() {}\n", "/api/fulfillment/tasks"),
        ("backend/services/loyalty.py", "def reward_referral_after_first_paid_order(): pass\n", "_lock_referral_customer"),
        ("scripts/backup_integrity.py", "KIND = 'broken'\n", "postgres_backup_manifest"),
        ("scripts/restore_postgres.sh", "#!/usr/bin/env bash\nexit 0\n", "verify-live"),
    ],
)
def test_immutable_archive_rejects_removed_guard_marker(
    tmp_path,
    path,
    replacement,
    expected_marker,
):
    repo = _guarded_repo(tmp_path)
    target = repo / path
    target.write_text(replacement, encoding="utf-8")
    _git(repo, "add", path)
    _git(repo, "commit", "-qm", f"remove guard from {path}")

    release = _release(repo, tmp_path, "unwired", "2026-08-05T00:02:00Z")
    errors = inspect_runtime_guard(release)
    assert any(path in error for error in errors)
    assert any(expected_marker in error for error in errors)
