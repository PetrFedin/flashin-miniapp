import shutil
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


def _guarded_repo(tmp_path: Path) -> Path:
    """Build the synthetic release from the real current capability surface.

    This intentionally avoids a hand-maintained copy of every marker. A new
    release capability must be proven by the exact files that production will
    package; marker-removal tests below mutate one copied file at a time.
    """

    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "pilot@example.com")
    _git(repo, "config", "user.name", "Pilot Test")
    for relative in sorted(REQUIRED_FILES):
        source = ROOT / relative
        assert source.is_file(), f"Required release capability source is missing: {relative}"
        target = repo / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
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
    assert CAPABILITY_VERSION == 33
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
    guarded = _release(repo, tmp_path, "guarded", "2026-08-10T00:00:00Z")
    assert inspect_runtime_guard(guarded) == []

    missing_path = repo / "scripts/moysklad_stock_authority_concurrency_smoke.py"
    missing_path.unlink()
    _git(repo, "add", "-u")
    _git(repo, "commit", "-qm", "remove authority concurrency proof")
    unguarded = _release(repo, tmp_path, "unguarded", "2026-08-10T00:01:00Z")
    errors = inspect_runtime_guard(unguarded)
    assert any("scripts/moysklad_stock_authority_concurrency_smoke.py" in error for error in errors)


@pytest.mark.parametrize(
    ("path", "replacement", "expected_marker"),
    [
        ("backend/api/payments.py", "class ProviderPaymentIntegrityError: pass\n", "trip_pilot_circuit_breaker"),
        ("backend/config.py", "class Settings: pass\n", "commercial_checkout_enabled: bool"),
        ("scripts/preflight.py", "print('Preflight OK')\n", "validate_admin_password_contract"),
        ("backend/tests/test_preflight_admin_password_contract.py", "def test_placeholder(): pass\n", "test_production_preflight_rejects_persisted_admin_password"),
        ("backend/services/runtime_capabilities.py", "def public_runtime_capabilities(): return {}\n", "require_payment_execution"),
        ("backend/services/privacy_export.py", "def write_customer_export(*args, **kwargs): pass\n", "decimal-string-2dp"),
        ("backend/api/privacy.py", "router = object()\n", "SpooledTemporaryFile"),
        ("scripts/privacy_export_postgres_smoke.py", "def main(): return 0\n", "privacy export smoke requires PostgreSQL"),
        ("backend/tests/test_privacy_export_contract.py", "def test_placeholder(): pass\n", "test_privacy_export_contract_is_exact_unicode_safe_and_customer_scoped"),
        ("backend/alembic/versions/0044_media_upload_commit_authority.py", "revision = '0044_media_upload_commit_authority'\n", "uq_media_assets_upload_key_nonempty"),
        ("backend/api/media.py", "router = object()\n", "authoritative_commit_reached"),
        ("admin/src/api.js", "export async function uploadAdminFile() {}\n", "Idempotency-Key"),
        ("backend/tests/test_media_storage_transaction_boundary.py", "def test_placeholder(): pass\n", "test_postcommit_response_failure_never_deletes_or_requeues_committed_object"),
        ("backend/tests/test_media_upload_commit_authority.py", "def test_placeholder(): pass\n", "test_create_all_mirrors_media_upload_key_partial_uniqueness"),
        ("scripts/media_storage_transaction_boundary_smoke.py", "def main(): return 0\n", "postcommit_asset_recovered"),
        ("backend/services/media_cleanup.py", "def enqueue_media_cleanup(*args, **kwargs): pass\n", "MEDIA_CLEANUP_PROVIDER"),
        ("backend/jobs/media_cleanup_jobs.py", "def process_media_cleanup_commands(*args, **kwargs): return {}\n", "authoritative_media_asset_id"),
        ("backend/services/media_storage.py", "async def save_media(*args, **kwargs): return {}\n", "MediaStorageWriteError"),
        ("backend/tests/test_media_async_transport.py", "def test_placeholder(): pass\n", "test_slow_s3_upload_does_not_block_event_loop"),
        ("backend/services/webhook_delivery.py", "SAFE_RETRY_PRE_DISPATCH = 'broken'\n", "ambiguous_transport"),
        ("backend/alembic/versions/0045_refund_item_allocation.py", "revision = '0045_refund_item_allocation'\n", "return_refund_allocations"),
        ("backend/refund_allocation_models.py", "class ReturnRefundAllocation: pass\n", "accounting/client evidence only"),
        ("backend/services/order_money_allocation.py", "def allocate_order_money(*args): return None\n", "ORDER_MONEY_POLICY_VERSION = 1"),
        ("backend/services/refund_allocation.py", "def ensure_refund_allocation(*args, **kwargs): pass\n", "reconcile_refund_allocations"),
        ("backend/tests/test_refund_item_allocation.py", "def test_placeholder(): pass\n", "test_quantity_backed_staged_refunds_use_exact_sequential_rounding"),
        ("scripts/refund_item_allocation_postgres_smoke.py", "def main(): return 0\n", "over_allocation_blocked"),
        ("docs/refunds/FINANCIAL_ITEM_ALLOCATION.md", "# Refunds\n", "financial refund does not restore sellable inventory"),
        ("backend/tests/test_webhook_outbox_ambiguity.py", "def test_placeholder(): pass\n", "test_non_2xx_http_outcomes_never_blindly_replay"),
        ("scripts/webhook_outbox_ambiguity_smoke.py", "def main(): return 0\n", "blind_replay_blocked"),
        ("docs/IDEMPOTENCY_CONTRACTS.md", "# Idempotency\n", "Receiver idempotency is not assumed"),
        ("scripts/media_cleanup_recovery_smoke.py", "def main(): return 0\n", "authoritative_media_reference_blocks_delete"),
        ("scripts/run_media_cleanup_jobs.py", "def main(): return 0\n", "media-cleanup"),
        ("docs/providers/object-storage.md", "# Object storage\n", "Durable cleanup command"),
        ("docs/runbooks/MEDIA_STORAGE_FAILURE.md", "# Runbook\n", "PostgreSQL outage / disaster recovery"),
        ("backend/alembic/versions/0043_moysklad_variant_identity_authority.py", "revision = '0043_moysklad_variant_identity_authority'\n", "uq_products_moysklad_id_nonempty"),
        ("backend/services/moysklad.py", "async def fetch_assortment(): pass\n", "MoySkladIdentityConflict"),
        ("backend/services/moysklad_outbound.py", "class MoySkladReviewRequired: pass\n", "exact MoySklad assortment id"),
        ("backend/tests/test_moysklad_variant_identity_authority.py", "def test_placeholder(): pass\n", "test_two_provider_variants_share_one_authoritative_parent_product"),
        ("backend/api/platform.py", "router = object()\n", '@router.get("/capabilities")'),
        ("frontend/src/App.jsx", "export default function App() {}\n", "SAFE_RUNTIME_CAPABILITIES"),
        ("e2e/tests/storefront.spec.js", "test(\"placeholder\", async () => {})\n", "provider-disabled production keeps non-money customer surfaces usable"),
        (".github/workflows/ci.yml", "jobs:\n  docker:\n    needs: [backend]\n", "browser-e2e"),
        ("backend/middleware/metrics.py", "def metrics_response(): pass\n", "flashin_pilot_metrics_collection_success"),
        ("admin/src/BusinessEventsPanel.jsx", "export default function Panel() {}\n", "ServiceOperationsPanel"),
        ("backend/api/support.py", "class AdminSupportTicketOut: pass\n", "assigned_admin_id"),
        ("admin/src/FulfillmentOperationsPanel.jsx", "export default function Panel() {}\n", "/api/fulfillment/tasks"),
        ("backend/services/loyalty.py", "def reward_referral_after_first_paid_order(): pass\n", "_lock_referral_customer"),
        ("scripts/backup_integrity.py", "KIND = 'broken'\n", "postgres_backup_manifest"),
        ("scripts/restore_postgres.sh", "#!/usr/bin/env bash\nexit 0\n", "verify-live"),
        ("scripts/deploy_release_gate.py", "#!/usr/bin/env python3\n", "retained under deploy/release/builds"),
        ("scripts/deploy_production.sh", "#!/usr/bin/env bash\n", "deploy_release_gate.py"),
        ("scripts/pilot_release_contract.py", "CAPABILITY_VERSION = 32\n", "CAPABILITY_VERSION = 33"),
        (
            "backend/middleware/rate_limit.py",
            "class RateLimitMiddleware: pass\n",
            "backend_fail_closed",
        ),
        (
            "backend/services/distributed_rate_limit.py",
            "class DistributedRateLimiter: pass\n",
            "_ATOMIC_SLIDING_WINDOW",
        ),
        (
            ".github/workflows/distributed-rate-limit-state.yml",
            "name: Distributed Rate Limit State\njobs: {}\n",
            "rate_limit_redis_smoke.py",
        ),
        (
            "docker-compose.yml",
            "services:\n  backend: {}\n",
            "redis:8.10.1-alpine",
        ),
        (
            "scripts/rate_limit_redis_smoke.py",
            "def main(): return 0\n",
            "two_clients_share_budget",
        ),
        (
            "scripts/rate_limit_persistence_smoke.sh",
            "#!/usr/bin/env bash\nexit 0\n",
            "budget_survived_restart",
        ),
        (
            "docs/runbooks/RATE_LIMIT_AUTHORITY.md",
            "# Rate limiting\n",
            "Fail closed when the shared limiter is unavailable",
        ),
        (
            "scripts/pilot_evidence.py",
            "CONFIG_FINGERPRINT_KEYS = (\"APP_ENV\",)\n",
            "\"RATE_LIMIT_BACKEND\"",
        ),
        (
            "backend/tests/test_pilot_configuration_fingerprint.py",
            "CRITICAL_WIRING_KEYS = (\"RATE_LIMIT_ENABLED\",)\n",
            "\"RATE_LIMIT_REDIS_URL\"",
        ),
        (
            "backend/services/inventory_movement_contract.py",
            "def movement_transition_valid(movement): return True\n",
            "expected_inventory_delta",
        ),
        (
            "backend/services/moysklad_reverse_return.py",
            "def enqueue_moysklad_physical_sales_return(*args, **kwargs): pass\n",
            "_ALLOCATION_VERSION = 2",
        ),
        (
            "backend/services/moysklad_stock_authority.py",
            "def evaluate_moysklad_stock_snapshot(*args, **kwargs): pass\n",
            "_is_open_evidence_unique_race",
        ),
        (
            "backend/alembic/versions/0041_reverse_logistics_authority.py",
            "revision = '0041_reverse_logistics_authority'\n",
            "_DOWNGRADE_BLOCKED",
        ),
        (
            "backend/alembic/versions/0042_moysklad_stock_evidence_concurrency.py",
            "revision = '0042_moysklad_stock_evidence_concurrency'\n",
            "uq_moysklad_conflict_open_stale_physical_return",
        ),
        (
            "backend/database.py",
            "class Base: pass\n",
            "_append_partial_unique_index",
        ),
        (
            ".github/workflows/reverse-logistics-state.yml",
            "name: Reverse Logistics State\njobs: {}\n",
            "moysklad_disposition_authority_smoke.py",
        ),
        (
            "backend/alembic/versions/0046_moysklad_return_disposition.py",
            "revision = '0046_moysklad_return_disposition'\n",
            "_DOWNGRADE_BLOCKED",
        ),
        (
            "backend/tests/test_moysklad_disposition_authority.py",
            "def test_placeholder(): pass\n",
            "test_mixed_return_creates_three_distinct_provider_outcomes",
        ),
        (
            "scripts/moysklad_disposition_authority_smoke.py",
            "def main(): return 0\n",
            "quarantine_resolution_concurrency",
        ),
        (
            "admin/src/PhysicalReturnPanel.jsx",
            "export default function Panel() {}\n",
            "/physical/quarantine/resolve",
        ),
        (
            "backend/tests/test_moysklad_reverse_return_allocation.py",
            "def test_placeholder(): pass\n",
            "test_sibling_partial_returns_allocate_exact_original_line_cents_without_rounding_drift",
        ),
        (
            "backend/tests/test_moysklad_stock_authority.py",
            "def test_placeholder(): pass\n",
            "test_blocked_operational_evidence_survives_business_transaction_rollback",
        ),
        (
            "backend/tests/test_moysklad_stock_authority_concurrency.py",
            "def test_placeholder(): pass\n",
            "test_only_owned_open_evidence_unique_races_are_retryable",
        ),
        (
            "scripts/moysklad_stock_authority_concurrency_smoke.py",
            "def main(): return 0\n",
            "WORKERS = 12",
        ),
        (
            "scripts/reverse_logistics_downgrade_guard_smoke.py",
            "def main(): return 0\n",
            "0046_moysklad_return_disposition",
        ),
        (
            "backend/tests/test_pilot_database_evidence.py",
            "def test_placeholder(): pass\n",
            "test_interleaved_same_sku_order_cannot_sign_other_orders_commit",
        ),
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

    release = _release(repo, tmp_path, "unwired", "2026-08-10T00:02:00Z")
    errors = inspect_runtime_guard(release)
    assert any(path in error for error in errors)
    assert any(expected_marker in error for error in errors)


def test_immutable_archive_rejects_missing_full_release_rollback_proof(tmp_path):
    repo = _guarded_repo(tmp_path)
    smoke = repo / "scripts/release_rollback_smoke.sh"
    smoke.write_text("ROLLBACK_DRILL=1\n", encoding="utf-8")
    _git(repo, "add", str(smoke.relative_to(repo)))
    _git(repo, "commit", "-qm", "remove full rollback proof")
    archive = _release(repo, tmp_path, "missing-full-rollback", "2026-08-10T00:03:00Z")

    errors = inspect_runtime_guard(archive)

    assert any("runtime_image_rebuilt" in error for error in errors)
    assert any("signed_evidence_verified" in error for error in errors)
