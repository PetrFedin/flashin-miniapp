#!/usr/bin/env python3
"""Inspect immutable releases and sign proof that pilot runtime rollback is supported."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
import zipfile
from pathlib import Path
from typing import Any, Mapping

from pilot_evidence import require_signing_secret, sign_payload, verify_payload_signature
from pilot_release_contract import CAPABILITY_VERSION
from pilot_readiness import read_env
from release_control import MANIFEST_NAME, sha256_file, verify_release

ROOT = Path(__file__).resolve().parents[1]
STATE_DIR = ROOT / "deploy/release/runtime"
CAPABILITY_NAME = "pilot_runtime_guard"

REQUIRED_FILES = {
    ".env.production.example",
    ".github/workflows/ci.yml",
    "admin/index.html",
    "admin/src/BusinessEventsPanel.jsx",
    "admin/src/FulfillmentOperationsPanel.jsx",
    "admin/src/ServiceOperationsPanel.jsx",
    "admin/src/fulfillmentOperations.js",
    "admin/src/fulfillmentOperations.test.js",
    "admin/src/serviceOperations.css",
    "admin/src/serviceOperations.js",
    "admin/src/serviceOperations.test.js",
    "backend/pilot_models.py",
    "backend/order_statuses.py",
    "backend/services/pilot_runtime.py",
    "backend/services/pilot_database_evidence.py",
    "backend/services/pilot_inventory_evidence.py",
    "backend/services/inventory.py",
    "backend/alembic/versions/0024_inventory_movement_ledger.py",
    "backend/services/pilot_circuit_breaker.py",
    "backend/services/payment_reconciliation.py",
    "backend/services/payment_settlement.py",
    "backend/services/loyalty.py",
    "backend/services/fulfillment.py",
    "backend/services/delivery_providers.py",
    "backend/alembic/versions/0022_pilot_runtime_guard.py",
    "backend/api/orders.py",
    "backend/api/payments.py",
    "backend/api/returns.py",
    "backend/api/support.py",
    "backend/api/fulfillment.py",
    "backend/api/delivery_providers.py",
    "backend/tests/test_support_admin_schema.py",
    "backend/tests/test_referral_attribution.py",
    "backend/tests/test_backup_integrity.py",
    "backend/tests/test_deploy_release_gate.py",
    "backend/main.py",
    "backend/middleware/metrics.py",
    "deploy/grafana/dashboards/flashin_operations.json",
    "deploy/grafana/provisioning/dashboards/dashboards.yml",
    "deploy/grafana/provisioning/datasources/prometheus.yml",
    "deploy/monitoring/prometheus.yml",
    "deploy/monitoring/rules/flashin_pilot.yml",
    "docs/pilot/end_to_end_coverage_matrix.md",
    "e2e/package.json",
    "e2e/playwright.config.js",
    "e2e/tests/admin.spec.js",
    "e2e/tests/fulfillment-admin.spec.js",
    "e2e/tests/owner-admin.spec.js",
    "e2e/tests/storefront.spec.js",
    "scripts/full_fulfillment_smoke.py",
    "scripts/referral_attribution_smoke.py",
    "scripts/backup_integrity.py",
    "scripts/backup_postgres.sh",
    "scripts/verify_backup.sh",
    "scripts/restore_postgres.sh",
    "scripts/backup_restore_smoke.sh",
    "scripts/release_rollback_smoke.sh",
    "scripts/readiness_gate.py",
    "scripts/pilot_admission.py",
    "backend/tests/test_pilot_admission.py",
    "scripts/pilot_control_binding.py",
    "scripts/pilot_control_audit.py",
    "scripts/pilot_control_chain.py",
    "scripts/pilot_control_lock.py",
    "scripts/pilot_control_io.py",
    "scripts/pilot_control.py",
    "backend/alembic/versions/0023_pilot_state_replay_anchor.py",
    "scripts/pilot_runner.py",
    "backend/tests/test_pilot_control_binding.py",
    "backend/tests/test_pilot_control_audit.py",
    "backend/tests/test_pilot_control_signature.py",
    "backend/tests/test_pilot_control_durability.py",
    "backend/tests/test_pilot_runtime.py",
    "backend/tests/test_pilot_database_evidence.py",
    "backend/tests/test_inventory_movement_ledger.py",
    "backend/tests/test_pilot_state_replay_migration.py",
    "Makefile",
    "scripts/pilot_runtime.py",
    "scripts/check_pilot_runtime_integrity.py",
    "scripts/pilot_release_capability.py",
    "scripts/pilot_release_contract.py",
    "scripts/check_production_compose.py",
    "scripts/deploy_release_gate.py",
    "docker-compose.yml",
    "docker-compose.production.yml",
    "scripts/deploy_production.sh",
    "scripts/rollback.sh",
}

# Production capability modes are part of the immutable runtime/rollback contract.
PRODUCTION_CAPABILITY_REQUIRED_FILES = {
    ".env.production.example",
    "backend/config.py",
    "backend/api/orders.py",
    "backend/api/payments.py",
    "backend/api/platform.py",
    "backend/jobs/moysklad_jobs.py",
    "backend/jobs/payment_jobs.py",
    "backend/jobs/provider_command_jobs.py",
    "backend/jobs/refund_jobs.py",
    "backend/services/diagnostics.py",
    "backend/services/moysklad.py",
    "backend/services/payments.py",
    "backend/services/runtime_capabilities.py",
    "backend/tests/test_production_config.py",
    "backend/tests/test_runtime_capabilities.py",
    "backend/tests/test_runtime_capability_diagnostics.py",
    "frontend/src/App.jsx",
    "frontend/src/api.js",
    "frontend/src/storefrontLoaders.js",
    "frontend/src/storefrontLoaders.test.js",
    "scripts/pilot_launch_preflight.py",
    "scripts/preflight.py",
    "backend/tests/test_preflight_admin_password_contract.py",
    "scripts/pilot_readiness.py",
    "scripts/readiness_gate.py",
    "scripts/validate_env.py",
}
REQUIRED_FILES |= PRODUCTION_CAPABILITY_REQUIRED_FILES

# Reverse-logistics/MoySklad stock authority is part of the immutable rollback
# capability, not merely incidental archive content. These files jointly define
# schema authority, physical evidence, signed inventory semantics, provider
# reconciliation, durable blocked evidence and the state workflow that proves it.
AUTHORITY_REQUIRED_FILES = {
    ".github/workflows/reverse-logistics-state.yml",
    "backend/alembic/versions/0041_reverse_logistics_authority.py",
    "backend/alembic/versions/0042_moysklad_stock_evidence_concurrency.py",
    "backend/database.py",
    "backend/models.py",
    "backend/reverse_logistics_models.py",
    "backend/services/inventory_movement_contract.py",
    "backend/services/moysklad_reverse_return.py",
    "backend/services/moysklad_stock_authority.py",
    "backend/services/pilot_inventory_evidence.py",
    "backend/services/pilot_inventory_safety.py",
    "backend/services/reverse_logistics.py",
    "backend/services/stock_reconciliation.py",
    "backend/tests/test_moysklad_reverse_return_allocation.py",
    "backend/tests/test_moysklad_stock_authority.py",
    "backend/tests/test_moysklad_stock_authority_concurrency.py",
    "backend/tests/test_pilot_database_evidence.py",
    "scripts/moysklad_reverse_logistics_contract_smoke.py",
    "scripts/moysklad_stock_authority_concurrency_smoke.py",
    "scripts/reverse_logistics_downgrade_guard_smoke.py",
    "scripts/reverse_logistics_state_smoke.py",
}
REQUIRED_FILES |= AUTHORITY_REQUIRED_FILES

# MoySklad provider identity is part of the immutable runtime and rollback contract.
MOYSKLAD_IDENTITY_REQUIRED_FILES = {
    ".github/workflows/ci.yml",
    "backend/alembic/versions/0043_moysklad_variant_identity_authority.py",
    "backend/database.py",
    "backend/services/moysklad.py",
    "backend/services/moysklad_outbound.py",
    "backend/services/moysklad_reverse_return.py",
    "backend/tests/test_moysklad_variant_identity_authority.py",
    "scripts/reverse_logistics_downgrade_guard_smoke.py",
}
REQUIRED_FILES |= MOYSKLAD_IDENTITY_REQUIRED_FILES

# Customer privacy export is part of the immutable production/rollback contract.
PRIVACY_EXPORT_REQUIRED_FILES = {
    ".github/workflows/ci.yml",
    "backend/api/privacy.py",
    "backend/main.py",
    "backend/services/privacy_export.py",
    "backend/tests/test_privacy_export_contract.py",
    "e2e/tests/storefront.spec.js",
    "frontend/src/App.jsx",
    "frontend/src/api.js",
    "scripts/privacy_export_postgres_smoke.py",
}
REQUIRED_FILES |= PRIVACY_EXPORT_REQUIRED_FILES

# Keep immutable capability semantics inspectable and maintainable. Every tuple
# binds one packaged runtime/test surface to concrete behavior, not just presence.
MARKER_REQUIREMENTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("backend/api/orders.py", ("acquire_pilot_checkout(", "record_pilot_order(")),
    ("scripts/pilot_release_contract.py", ("CAPABILITY_VERSION = 25",)),
    (
        "scripts/pilot_release_capability.py",
        (
            "from pilot_release_contract import CAPABILITY_VERSION",
            "PRODUCTION_CAPABILITY_REQUIRED_FILES",
            "REQUIRED_FILES |= PRODUCTION_CAPABILITY_REQUIRED_FILES",
            "AUTHORITY_REQUIRED_FILES",
            "REQUIRED_FILES |= AUTHORITY_REQUIRED_FILES",
            "MOYSKLAD_IDENTITY_REQUIRED_FILES",
            "REQUIRED_FILES |= MOYSKLAD_IDENTITY_REQUIRED_FILES",
            "PRIVACY_EXPORT_REQUIRED_FILES",
            "REQUIRED_FILES |= PRIVACY_EXPORT_REQUIRED_FILES",
            "MARKER_REQUIREMENTS",
        ),
    ),
    (
        ".env.production.example",
        ("COMMERCIAL_CHECKOUT_ENABLED=false", "PAYMENTS_MODE=disabled", "MOYSKLAD_MODE=disabled"),
    ),
    (
        "backend/config.py",
        (
            "commercial_checkout_enabled: bool",
            "payments_mode: str",
            "moysklad_mode: str",
            "PAYMENTS_MODE must be disabled when production commercial checkout is disabled",
            "PILOT_RUNTIME_ENFORCED must be false when production commercial checkout is disabled",
        ),
    ),
    (
        "backend/services/runtime_capabilities.py",
        (
            "def payment_execution_enabled(",
            "def moysklad_execution_enabled(",
            "def require_commercial_checkout(",
            "def require_payment_execution(",
            "def require_moysklad_execution(",
            "def public_runtime_capabilities(",
        ),
    ),
    ("backend/api/platform.py", ('@router.get("/capabilities")', "return public_runtime_capabilities()")),
    ("backend/api/orders.py", ("require_commercial_checkout", "require_commercial_checkout()", "acquire_pilot_checkout(")),
    ("backend/api/payments.py", ("require_payment_execution", "ProviderPaymentIntegrityError", "trip_pilot_circuit_breaker(")),
    ("backend/services/payments.py", ("require_payment_execution", "async def _request_yookassa(")),
    ("backend/services/moysklad.py", ("require_moysklad_execution", "async def fetch_assortment(")),
    ("backend/jobs/payment_jobs.py", ("payment_execution_enabled", "reconcile_pending_payments")),
    ("backend/jobs/refund_jobs.py", ("payment_execution_enabled", "reconcile_pending_refunds")),
    ("backend/jobs/provider_command_jobs.py", ("moysklad_execution_enabled", "process_provider_commands")),
    ("backend/jobs/moysklad_jobs.py", ("moysklad_execution_enabled", "run_moysklad_pipeline")),
    (
        "backend/services/diagnostics.py",
        ("payment_execution_enabled(settings)", "moysklad_execution_enabled(settings)", '"status": "disabled"'),
    ),
    (
        "scripts/validate_env.py",
        ('"COMMERCIAL_CHECKOUT_ENABLED"', '"PAYMENTS_MODE"', '"MOYSKLAD_MODE"', "commercial_checkout_enabled ="),
    ),
    (
        "scripts/pilot_launch_preflight.py",
        (
            "COMMERCIAL_CHECKOUT_ENABLED must be true for pilot runtime arm",
            "PAYMENTS_MODE must be sandbox or live for pilot runtime arm",
            "MOYSKLAD_MODE must be sandbox or live for pilot runtime arm",
        ),
    ),
    (
        "scripts/preflight.py",
        (
            "def required_env_keys(",
            "def validate_admin_password_contract(",
            'if app_env == "production":',
            "ADMIN_PASSWORD must not be stored in production",
            'BASE_REQUIRED_ENV_KEYS + ("ADMIN_PASSWORD",)',
        ),
    ),
    (
        "backend/tests/test_preflight_admin_password_contract.py",
        (
            "test_production_preflight_accepts_env_without_admin_password",
            "test_production_preflight_rejects_persisted_admin_password",
            "test_nonproduction_preflight_still_requires_admin_password",
        ),
    ),
    (
        "frontend/src/App.jsx",
        ("SAFE_RUNTIME_CAPABILITIES", "commercialCheckoutEnabled", "paymentsEnabled", "Онлайн-оформление сейчас отключено."),
    ),
    ("frontend/src/storefrontLoaders.js", ("SAFE_CAPABILITY_FALLBACK", "api.getPlatformCapabilities()")),
    (
        "e2e/tests/storefront.spec.js",
        (
            "provider-disabled production keeps non-money customer surfaces usable",
            "PROVIDER_DISABLED_RUNTIME_CAPABILITIES",
            "commercialMutationAttempts",
            "made_to_order",
            "Онлайн-оформление сейчас отключено.",
        ),
    ),
    (
        "backend/tests/test_runtime_capabilities.py",
        (
            "test_checkout_is_rejected_before_idempotency_or_database_mutation",
            "test_payment_creation_is_rejected_before_attempt_claim",
            "test_low_level_yookassa_transport_never_constructs_http_client_when_disabled",
            "test_disabled_background_jobs_do_not_touch_database_or_provider",
        ),
    ),
    (
        "backend/tests/test_production_config.py",
        (
            "test_provider_disabled_production_does_not_require_commerce_credentials_or_pilot_state",
            "test_production_rejects_enabled_payments_when_checkout_is_disabled",
        ),
    ),
    (
        "backend/alembic/versions/0043_moysklad_variant_identity_authority.py",
        (
            "0043_moysklad_variant_identity_authority",
            "0042_moysklad_stock_evidence_concurrency",
            "def _assert_unique_provider_id_data(",
            "uq_products_moysklad_id_nonempty",
            "uq_product_variants_moysklad_id_nonempty",
            "resolve identity evidence explicitly before migration",
        ),
    ),
    (
        "backend/database.py",
        (
            "uq_products_moysklad_id_nonempty",
            "uq_product_variants_moysklad_id_nonempty",
            "moysklad_id <> ''",
        ),
    ),
    (
        "backend/services/moysklad.py",
        (
            "class MoySkladIdentityConflict",
            "def _resolve_assortment_identity(",
            "missing_parent_identity",
            "parent_reassignment",
            "provider_id_collision",
            "def _placeholder_parent_sku(",
            "sku_variant.moysklad_id = row_provider_id",
        ),
    ),
    (
        "backend/services/moysklad_outbound.py",
        (
            'str(variant.moysklad_id or "").strip()',
            "exact MoySklad assortment id",
        ),
    ),
    (
        "backend/services/moysklad_reverse_return.py",
        (
            'str(variant.moysklad_id or "").strip()',
            "Physical return variant has no exact MoySklad assortment mapping",
        ),
    ),
    (
        "backend/tests/test_moysklad_variant_identity_authority.py",
        (
            "test_two_provider_variants_share_one_authoritative_parent_product",
            "test_provider_variant_sku_rename_updates_same_local_identity",
            "test_same_provider_variant_under_another_parent_fails_closed_without_phantom_product",
            "test_migration_rejects_duplicate_legacy_provider_identity_without_mutating_rows",
        ),
    ),
    (
        ".github/workflows/ci.yml",
        (
            "Prove MoySklad variant identity authority",
            "backend/tests/test_moysklad_variant_identity_authority.py",
        ),
    ),
    (
        "scripts/reverse_logistics_downgrade_guard_smoke.py",
        (
            "0043_moysklad_variant_identity_authority",
            "uq_products_moysklad_id_nonempty",
            "uq_product_variants_moysklad_id_nonempty",
            "provider_identity_indexes_preserved",
        ),
    ),
    (
        "backend/services/privacy_export.py",
        (
            'PRIVACY_EXPORT_SCHEMA_VERSION = "flashin.customer-data-export.v1"',
            "PRIVACY_EXPORT_BATCH_SIZE = 200",
            "def _decimal_string(",
            '"money": "decimal-string-2dp"',
            '"loyalty_points": "decimal-string-4dp"',
            "def _iter_customer_rows(",
            "def write_customer_export(",
            "Anonymized customer identities cannot be exported",
        ),
    ),
    (
        "backend/api/privacy.py",
        (
            "SpooledTemporaryFile",
            "write_customer_export(db, customer, export_file)",
            "StreamingResponse(",
            '"Content-Disposition"',
            '"Content-Length"',
            '"Cache-Control": "no-store, max-age=0"',
        ),
    ),
    (
        "backend/main.py",
        ('expose_headers=["X-Request-ID", "Content-Disposition"]',),
    ),
    (
        "backend/tests/test_privacy_export_contract.py",
        (
            "test_privacy_export_contract_is_exact_unicode_safe_and_customer_scoped",
            "test_privacy_export_batch_iterator_is_keyset_bounded_and_stable",
            "test_anonymized_customer_export_is_explicitly_unavailable",
        ),
    ),
    (
        "scripts/privacy_export_postgres_smoke.py",
        (
            "privacy export smoke requires PostgreSQL",
            'client.get("/api/privacy/export")',
            '"decimal_exact": True',
            '"ownership_isolated": True',
        ),
    ),
    (
        ".github/workflows/ci.yml",
        (
            "Prove PostgreSQL privacy export contract",
            "backend/tests/test_privacy_export_contract.py",
            "python scripts/privacy_export_postgres_smoke.py",
        ),
    ),
    (
        "frontend/src/api.js",
        (
            "export async function downloadPrivacyData()",
            'response.headers.get("content-disposition")',
            '"flashin_customer_export.json"',
        ),
    ),
    (
        "frontend/src/App.jsx",
        (
            "handlePrivacyExport",
            "URL.createObjectURL(exported.blob)",
            "Скачать мои данные",
        ),
    ),
    (
        "e2e/tests/storefront.spec.js",
        (
            "flashin.customer-data-export.v1",
            "download.suggestedFilename()",
            'readFile(downloadPath, "utf8")',
        ),
    ),
    (
        "backend/services/pilot_runtime.py",
        ("from scripts.pilot_release_contract import CAPABILITY_VERSION", '"version": CAPABILITY_VERSION'),
    ),
    (
        "backend/services/pilot_database_evidence.py",
        (
            "def validate_pilot_database_evidence(",
            "pilot slot order_id",
            "PostgreSQL payment",
            "PostgreSQL refund",
            "final GO scenario order IDs",
        ),
    ),
    (
        "backend/services/pilot_inventory_evidence.py",
        (
            "def validate_order_inventory_evidence(",
            "reserve/release",
            "reserve/commit",
            "signed stock_before",
            "signed expected_stock_delta",
            "scoped inventory movement delta",
            "expected inventory contract delta",
        ),
    ),
    (
        "backend/services/inventory.py",
        ("InventoryMovement(", 'kind="reserve"', 'kind="release"', 'kind="commit"', "order_id=order_id"),
    ),
    (
        "backend/alembic/versions/0024_inventory_movement_ledger.py",
        ("0024_inventory_movement_ledger", "0023_pilot_state_replay_anchor", "inventory_movements", "uq_inventory_movement_order_variant_kind"),
    ),
    (
        "backend/tests/test_inventory_movement_ledger.py",
        (
            "test_reserve_and_release_are_one_durable_order_linked_chain",
            "test_reserve_and_commit_capture_stock_and_reserved_snapshots",
            "test_production_inventory_callsites_are_order_attributed",
        ),
    ),
    (
        "backend/tests/test_pilot_database_evidence.py",
        (
            "test_exact_completed_twenty_order_database_evidence_is_accepted",
            "test_missing_or_wrong_slot_order_fails_closed",
            "test_payment_refund_status_and_amount_are_read_from_postgresql",
            "test_final_go_rejects_active_or_incomplete_runtime",
            "test_interleaved_same_sku_order_proof_uses_only_its_own_movement_delta",
            "test_interleaved_same_sku_order_cannot_sign_other_orders_commit",
            "test_expected_inventory_delta_is_semantic_and_order_local",
        ),
    ),
    (
        ".github/workflows/reverse-logistics-state.yml",
        (
            "name: Reverse Logistics State",
            "python scripts/reverse_logistics_state_smoke.py",
            "python scripts/moysklad_reverse_logistics_contract_smoke.py",
            "python -m pytest -q backend/tests/test_moysklad_reverse_return_allocation.py",
            "python scripts/moysklad_stock_authority_concurrency_smoke.py",
            "python scripts/reverse_logistics_downgrade_guard_smoke.py",
        ),
    ),
    (
        "backend/alembic/versions/0041_reverse_logistics_authority.py",
        ("0041_reverse_logistics_authority", "_DOWNGRADE_BLOCKED", "return_logistics_events", "uq_inventory_movement_reverse_event_source"),
    ),
    (
        "backend/alembic/versions/0042_moysklad_stock_evidence_concurrency.py",
        (
            "0042_moysklad_stock_evidence_concurrency",
            "0041_reverse_logistics_authority",
            "_collapse_duplicate_open_evidence",
            "uq_moysklad_conflict_open_stale_physical_return",
            "uq_stock_reconciliation_open_blocked_physical_return",
        ),
    ),
    (
        "backend/database.py",
        (
            "_append_partial_unique_index",
            "uq_moysklad_conflict_open_stale_physical_return",
            "uq_stock_reconciliation_open_blocked_physical_return",
        ),
    ),
    ("backend/models.py", ("class MoySkladConflict", "class StockReconciliationLog")),
    (
        "backend/reverse_logistics_models.py",
        ("class ReturnLogisticsCase", "class ReturnLogisticsItem", "class ReturnLogisticsEvent", "uq_return_logistics_case_idempotency"),
    ),
    (
        "backend/services/inventory_movement_contract.py",
        ("def movement_transition_valid(", "def expected_inventory_delta(", "Snapshot continuity is deliberately *not* required", 'if movement.kind == "return"'),
    ),
    (
        "backend/services/reverse_logistics.py",
        ("def inspect_item(", 'normalized_disposition == "resalable"', 'kind="return"', 'source=f"reverse_logistics_event:{event.id}"'),
    ),
    (
        "backend/services/moysklad_reverse_return.py",
        (
            "_ALLOCATION_VERSION = 2",
            "def _build_physical_return_allocation(",
            "def enqueue_moysklad_physical_sales_return(",
            "moysklad.physical_sales_return.create",
            "Immutable physical return monetary allocation does not match physical evidence",
            "allocation_error",
            "Damaged/quarantine physical return requires provider disposition reconciliation",
            "Only fully resalable physical quantities can be auto-exported",
        ),
    ),
    (
        "backend/services/moysklad_stock_authority.py",
        (
            "def evaluate_moysklad_stock_snapshot(",
            "_STALE_PHYSICAL_RETURN_CONFLICT",
            "_BLOCKED_RECONCILIATION_ACTION",
            "_OPEN_EVIDENCE_CONSTRAINTS",
            "def _is_open_evidence_unique_race(",
            "def _persist_blocked_evidence_durably(",
            "No ProductVariant/SKU lock is introduced",
            "catch-up/resolution remains in the caller transaction",
        ),
    ),
    (
        "backend/services/stock_reconciliation.py",
        ("evaluate_moysklad_stock_snapshot", "if decision.blocked:", "db.commit()"),
    ),
    (
        "backend/tests/test_moysklad_reverse_return_allocation.py",
        (
            "test_sibling_partial_returns_allocate_exact_original_line_cents_without_rounding_drift",
            "assert allocations == [34, 34, 33]",
            "test_tampered_physical_return_money_allocation_fails_closed",
            "test_allocation_failure_persists_owned_fail_closed_provider_evidence",
        ),
    ),
    (
        "backend/tests/test_moysklad_stock_authority.py",
        (
            "test_stale_provider_snapshot_cannot_erase_verified_resalable_stock",
            "test_actual_provider_transaction_and_stock_catchup_release_guard",
            "test_blocked_operational_evidence_survives_business_transaction_rollback",
        ),
    ),
    (
        "backend/tests/test_moysklad_stock_authority_concurrency.py",
        (
            "test_create_all_mirrors_moysklad_open_evidence_unique_indexes",
            "test_only_owned_open_evidence_unique_races_are_retryable",
            "test_sqlite_owned_unique_messages_are_retryable_without_masking_others",
        ),
    ),
    ("scripts/reverse_logistics_state_smoke.py", ("reverse", "logistics")),
    (
        "scripts/moysklad_reverse_logistics_contract_smoke.py",
        ("moysklad", "physical", "immutable_money_allocation"),
    ),
    (
        "scripts/moysklad_stock_authority_concurrency_smoke.py",
        (
            "WORKERS = 12",
            "Barrier(WORKERS)",
            "evaluate_moysklad_stock_snapshot",
            "one open evidence pair",
        ),
    ),
    (
        "scripts/reverse_logistics_downgrade_guard_smoke.py",
        (
            "downgrade",
            "0041",
            "0043_moysklad_variant_identity_authority",
            "uq_moysklad_conflict_open_stale_physical_return",
            "uq_stock_reconciliation_open_blocked_physical_return",
            "uq_products_moysklad_id_nonempty",
            "uq_product_variants_moysklad_id_nonempty",
        ),
    ),
    (
        "scripts/readiness_gate.py",
        ("def build_signed_live_report(", '"kind": "pilot_live_gate"', "configuration_fingerprint(env, secret)", "release_binding(current_release)", "return sign_payload(payload, secret)"),
    ),
    (
        "scripts/pilot_admission.py",
        ("live gate evidence signature is invalid", "live gate configuration fingerprint does not match", "live gate release binding is missing", "validate_release_binding(release, current_release)", "def validate_admission_evidence_inputs(", "current_release=current_release"),
    ),
    (
        "backend/tests/test_pilot_admission.py",
        ("test_live_gate_rejects_tampering_configuration_and_other_release", "test_admission_create_preflight_binds_live_gate_to_current_release", "configuration fingerprint", "live gate release"),
    ),
    (
        "scripts/pilot_control_binding.py",
        ("def build_admission_binding(", "manifest_sha256", "def validate_admission_binding(", "def require_admission_binding("),
    ),
    (
        "scripts/pilot_control.py",
        ("SCHEMA_VERSION = 7", "database_evidence_contract", "inventory_evidence_contract", "verified_admission_context(", "approved_operator_names=args.approved_operators", "mutation=_mutation_from_args(", "Unattributed pilot state schema 4 cannot be reused", "Last accountable mutation"),
    ),
    ("scripts/pilot_runner.py", ("errors = verify_default_admission(ROOT)", "return pilot_control_main(args)")),
    (
        "backend/services/pilot_runtime.py",
        ("build_admission_binding(manifest_path, manifest)", "validate_state_descendant(", "validate_audit_log(", "approved_operators(manifest)", "validate_pilot_database_evidence(", "state.pilot_state_revision", "armed runtime pilot state replay anchor is missing"),
    ),
    (
        "scripts/pilot_runtime.py",
        ("build_admission_binding(DEFAULT_MANIFEST, manifest)", "validate_audit_log(", "approved_operators(manifest)", "validate_pilot_database_evidence(", "pilot_state_revision", "validate_anchor_transition(", "Stopped pilot runtime cannot change admission or release lineage"),
    ),
    (
        "Makefile",
        ("python3 scripts/pilot_runner.py init $(ARGS)", "--operator-role operations_owner", "python3 scripts/pilot_runner.py record $(ARGS)", "python3 scripts/pilot_runner.py status", "python3 scripts/pilot_runner.py validate --final"),
    ),
    (
        "backend/tests/test_pilot_control_binding.py",
        ("test_state_is_bound_to_one_exact_signed_admission_file", "test_legacy_state_is_rejected_without_silent_migration", "test_makefile_routes_pilot_control_through_admission_runner"),
    ),
    (
        "backend/tests/test_pilot_control_audit.py",
        ("test_init_and_record_are_bound_to_admission_owners_and_lineage", "test_unapproved_name_or_role_is_rejected", "test_misleading_scenario_audit_is_rejected", "test_tampered_or_unapproved_audit_fails_state_load", "test_init_and_record_parser_require_accountable_identity"),
    ),
    (
        "scripts/pilot_control_audit.py",
        ("APPROVAL_ROLES", "def approved_operators(", "def normalize_mutation(", "def validate_audit_log(", "def validate_record_mutation(", "does not match signed admission owner"),
    ),
    (
        "scripts/pilot_control_chain.py",
        ("def signed_state_sha256(", "def validate_anchor_transition(", "pilot control state revision rollback detected", "pilot control state ancestry does not match the armed runtime"),
    ),
    (
        "scripts/pilot_control_lock.py",
        ("def exclusive_state_lock(", "fcntl.LOCK_EX | fcntl.LOCK_NB", "Pilot control state lock acquisition timed out", "os.fchmod(handle.fileno(), 0o600)"),
    ),
    (
        "scripts/pilot_control_io.py",
        ("def durable_atomic_write_text(", "os.fsync(handle.fileno())", "os.replace(temporary_path, path)", "_fsync_directory(path.parent)", "os.fchmod(handle.fileno(), 0o600)"),
    ),
    ("backend/pilot_models.py", ("pilot_state_revision", "pilot_state_sha256", "ck_pilot_runtime_state_anchor")),
    (
        "backend/alembic/versions/0023_pilot_state_replay_anchor.py",
        ("0023_pilot_state_replay_anchor", "0022_pilot_runtime_guard", "pilot_state_revision", "pilot_state_sha256"),
    ),
    (
        "backend/tests/test_pilot_control_signature.py",
        ("test_cross_process_writers_serialize_and_reject_stale_parent", "test_cross_process_lock_timeout_fails_closed", 'multiprocessing.get_context("fork")'),
    ),
    (
        "backend/tests/test_pilot_control_durability.py",
        ("test_durable_atomic_write_fsyncs_file_and_parent_directory", "test_summary_refresh_repairs_stale_file_without_advancing_state", "test_summary_write_failure_leaves_valid_committed_state_and_is_repairable", "test_status_summary_refresh_does_not_change_signed_json_bytes"),
    ),
    (
        "backend/tests/test_pilot_runtime.py",
        ("test_tampered_pilot_control_state_fails_closed_on_checkout", "test_runtime_anchor_advances_to_descendant_and_rejects_replay", "test_unrelated_valid_signed_state_branch_fails_closed"),
    ),
    (
        "backend/services/pilot_circuit_breaker.py",
        ("def stop_pilot_for_order(", "def trip_pilot_circuit_breaker("),
    ),
    (
        "backend/api/payments.py",
        ("ProviderPaymentIntegrityError", "trip_pilot_circuit_breaker(", "stop_pilot_for_order("),
    ),
    ("backend/api/returns.py", ("trip_pilot_circuit_breaker(", "stop_pilot_for_order(")),
    (
        "backend/api/support.py",
        ("class AdminSupportTicketOut", "assigned_admin_id: int | None = None", "response_model=list[AdminSupportTicketOut]", "response_model=AdminSupportTicketOut"),
    ),
    (
        "backend/tests/test_support_admin_schema.py",
        ("test_admin_support_ticket_schema_exposes_accountable_owner", "assigned_admin_id"),
    ),
    ("backend/services/payment_reconciliation.py", ("payment_reconciliation_mismatch", "stop_pilot_for_order(")),
    ("backend/order_statuses.py", ("SETTLED_ORDER_PAYMENT_STATUSES", '"paid_review_required"', '"refund_review_required"')),
    (
        "backend/services/payment_settlement.py",
        ("from ..order_statuses import SETTLED_ORDER_PAYMENT_STATUSES", "reward_referral_after_first_paid_order(db, order.customer_id, order.id)", "if order.payment_status in SETTLED_ORDER_PAYMENT_STATUSES"),
    ),
    (
        "backend/services/loyalty.py",
        ("def _lock_referral_customer(", "def _has_prior_settled_order(", "Referral code must be applied before the first paid order", "return attach_referral_to_customer(db, code, new_customer_id)", 'attribution.status = "ineligible"', "def reward_referral_after_first_paid_order("),
    ),
    (
        "backend/tests/test_referral_attribution.py",
        ("test_legacy_apply_referral_only_attaches_pending_attribution", "test_referral_after_settled_order_is_rejected_even_for_same_code", "test_missing_customer_is_not_silently_eligible"),
    ),
    (
        "backend/services/fulfillment.py",
        ("def _picklist_is_complete(", "Every picklist item must be fully picked before packing", 'order.delivery_status = "ready"'),
    ),
    (
        "backend/api/fulfillment.py",
        ("fulfillment.task.update", "fulfillment.task_item.update", "assigned_admin_id"),
    ),
    (
        "backend/services/delivery_providers.py",
        ("_SHIPMENT_TRANSITIONS", "Only a ready order can be transferred to delivery", 'order.status = "shipped"', 'order.status = "completed"'),
    ),
    (
        "backend/api/delivery_providers.py",
        ("delivery.shipment.create", "delivery.shipment.update", "with_for_update()"),
    ),
    ("backend/main.py", ("collect_pilot_metrics", '@app.get("/metrics"', "return metrics_response()")),
    (
        "backend/middleware/metrics.py",
        ("flashin_pilot_metrics_collection_success", "def collect_pilot_metrics(", 'return "__unmatched__"'),
    ),
    (
        "deploy/monitoring/rules/flashin_pilot.yml",
        ("FlashinPilotMetricsUnavailable", "FlashinPilotArtifactIntegrityFailed", "FlashinPilotMoneyAttentionRequired", "FlashinPilotCapacityLow"),
    ),
    (
        "deploy/grafana/dashboards/flashin_operations.json",
        ("FLASHIN Operations", "flashin_pilot_checkout_ready", "flashin_pilot_money_attention"),
    ),
    ("deploy/grafana/provisioning/datasources/prometheus.yml", ("prometheus", "http://prometheus:9090")),
    ("deploy/monitoring/prometheus.yml", ("rule_files", "/etc/prometheus/rules/*.yml", "backend:8000")),
    (
        "scripts/check_production_compose.py",
        ('MONITORING_SERVICES = {"prometheus", "grafana"}', 'PRODUCTION_PROFILES = ("production", "workers", "scheduler", "search", "monitoring")', "Grafana anonymous access must be disabled"),
    ),
    (".env.production.example", ("METRICS_ENABLED=true", "GRAFANA_ADMIN_USER=", "GRAFANA_ADMIN_PASSWORD=")),
    ("docker-compose.yml", ("prometheus:", "grafana:", "prometheus_data", "grafana_data")),
    (
        ".github/workflows/ci.yml",
        ("browser-e2e:", "Install Chromium", "Run Mini App and Admin browser journeys", "Run transactional referral attribution smoke", "Run transactional full fulfillment smoke", "Run signed backup and restore drill", "bash scripts/backup_restore_smoke.sh", "Run signed full release rollback drill", "bash scripts/release_rollback_smoke.sh", "needs: [backend, frontend, admin, browser-e2e]"),
    ),
    ("e2e/package.json", ('"@playwright/test": "1.54.2"', '"test": "playwright test"')),
    (
        "e2e/playwright.config.js",
        ('name: "storefront-mobile"', 'name: "admin-desktop"', 'trace: "retain-on-failure"', 'screenshot: "only-on-failure"', 'video: "retain-on-failure"'),
    ),
    (
        "e2e/tests/storefront.spec.js",
        ("Mini App critical pilot journey", "Mini App cart quantity and removal controls", "Mini App profile, support, privacy and return journey", "Mini App payment return route refreshes paid order"),
    ),
    (
        "e2e/tests/admin.spec.js",
        ("Admin critical pilot operator journey", "Admin operations, fulfillment and BusinessEvent recovery journey", "Admin completes support, privacy and refund service operations"),
    ),
    (
        "e2e/tests/owner-admin.spec.js",
        ("Admin assigns an accountable owner to a support ticket", "assigned_admin_id: 42", "Ответственный обращения 901"),
    ),
    (
        "e2e/tests/fulfillment-admin.spec.js",
        ("Admin completes picklist, shipment and delivery lifecycle", "Собрать все позиции и упаковать", "PILOT-TRACK-9100", 'status: "completed"'),
    ),
    (
        "admin/src/FulfillmentOperationsPanel.jsx",
        ('"/api/fulfillment/tasks"', '"/api/delivery-providers/shipments"', "async function pickAndPack(", "async function ship(", "async function deliver("),
    ),
    (
        "admin/src/fulfillmentOperations.js",
        ("export function isPicklistComplete(", "export function fulfillmentAction(", "export function normalizeTracking(", "export function fulfillmentAttentionCount(", "Собрать все позиции и упаковать", "Передать в доставку", "Подтвердить доставку"),
    ),
    (
        "admin/src/fulfillmentOperations.test.js",
        ("fulfillment actions expose only the next safe workflow step", "picklist completeness requires every ordered unit", "tracking is bounded and meaningful", "attention remains until shipment is delivered"),
    ),
    (
        "admin/src/ServiceOperationsPanel.jsx",
        ('support: "/api/support/admin/tickets"', 'privacy: "/api/privacy/admin/requests"', 'returns: "/api/admin/returns"', 'adminJson("/api/returns/admin/approve"', "Подтвердить refund", "Ответственный обращения"),
    ),
    (
        "admin/src/serviceOperations.js",
        ("export function supportTransitions(", "export function canProcessPrivacy(", "export function canApproveReturn(", "export function normalizeAdminAssignment(", "export function normalizeRefundAmount(", "export function serviceAttentionCount("),
    ),
    (
        "admin/src/serviceOperations.test.js",
        ("support transitions follow the backend state machine", "support owner assignment accepts only positive integer Admin IDs", "refund amount is positive, bounded and rounded", "aggregate attention are fail-closed"),
    ),
    (
        "admin/src/BusinessEventsPanel.jsx",
        ('import FulfillmentOperationsPanel from "./FulfillmentOperationsPanel.jsx"', '<FulfillmentOperationsPanel onUnauthorized={onUnauthorized} />', 'import ServiceOperationsPanel from "./ServiceOperationsPanel.jsx"', "<ServiceOperationsPanel onUnauthorized={onUnauthorized} />"),
    ),
    ("admin/index.html", ('href="/src/serviceOperations.css"', "FLASHIN Admin")),
    ("admin/src/serviceOperations.css", (".service-operations", ".service-grid", ".attention-badge")),
    (
        "scripts/full_fulfillment_smoke.py",
        ("Every picklist item must be fully picked before packing", "idempotent shipment create", 'persisted_order.status == "completed"', 'persisted_order.delivery_status == "delivered"'),
    ),
    (
        "scripts/referral_attribution_smoke.py",
        ("duplicate referral payment webhook", "late_referral.status_code == 409", "persisted_referral.used_count == 1", "len(reward_rows) == 1", "second_persisted_order.referral_code is None"),
    ),
    (
        "scripts/backup_integrity.py",
        ('KIND = "postgres_backup_manifest"', "CRITICAL_TABLES = (", "def snapshot_database(", "def verify_restorable(", "def verify_live_database(", "backup SHA-256 does not match signed manifest", "restored critical table"),
    ),
    (
        "scripts/backup_postgres.sh",
        ("MANIFEST_FILE=", 'python3 "$INTEGRITY_SCRIPT" create', "Backup created, restored in isolation and signed"),
    ),
    (
        "scripts/verify_backup.sh",
        ("Signed backup manifest not found", 'python3 "$INTEGRITY_SCRIPT" verify', "Backup signature, archive, schema and critical data verification OK"),
    ),
    (
        "scripts/restore_postgres.sh",
        ("Signed backup manifest not found", 'python3 "$INTEGRITY_SCRIPT" verify', 'python3 "$INTEGRITY_SCRIPT" verify-live', "signed snapshot verified"),
    ),
    (
        "scripts/backup_restore_smoke.sh",
        ("tampered_archive_rejected", "mutated_database_rejected", "restored_value_verified", "verify-live", "restore_postgres.sh --yes"),
    ),
    (
        "scripts/release_rollback_smoke.sh",
        ("ROLLBACK_DRILL=1", "PREVIOUS_MARKER=", "CURRENT_MARKER=", "container_marker=", "restored_name=", "verify-live", "verify --slot both", "verify-rollback", "runtime_image_rebuilt", "release_pointer_promoted", "signed_evidence_verified"),
    ),
    (
        "backend/tests/test_backup_integrity.py",
        ("test_signed_manifest_binds_exact_archive_and_snapshot", "test_archive_byte_or_size_change_is_rejected", "test_snapshot_comparison_detects_schema_revision_and_ledger_changes", "test_database_identifiers_fail_closed"),
    ),
    (
        "scripts/deploy_release_gate.py",
        ("deployment release archive must be retained under deploy/release/builds", "--untracked-files=all", "release manifest git_commit does not match checkout HEAD", "release manifest file set does not match deploy checkout", "deploy checkout file differs from release artifact", "deploy checkout executable mode differs from release artifact"),
    ),
    (
        "backend/tests/test_deploy_release_gate.py",
        ("test_exact_retained_release_and_clean_checkout_are_accepted", "test_nonignored_untracked_build_context_is_rejected", "test_archive_from_other_commit_is_rejected", "test_non_executable_permission_differences_are_tolerated", "test_executable_mode_drift_is_rejected", "test_deploy_verifies_release_before_runtime_stop_and_builds_from_extracted_artifact"),
    ),
    (
        "scripts/deploy_production.sh",
        ("Verifying retained immutable Release artifact before any runtime mutation", "deploy_release_gate.py --archive", "release_control.py extract", 'cd "$release_source_dir"', "Building images from verified immutable Release artifact", "RELEASE=deploy/release/builds/flashin_<release>.zip make deploy-prod"),
    ),
    (
        "docs/pilot/end_to_end_coverage_matrix.md",
        ("## Browser journeys", "Nine stateful Playwright journeys", "accountable active Admin ID", "Admin service operations", "full picklist", "## Transactional referral evidence", "first paid order -> one inviter reward", "## Signed backup and restore evidence", "Backup/restore integrity", "Release rollback", "## Evidence boundary"),
    ),
)


def load_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"Release state not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"Release state is invalid JSON: {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"Release state must contain a JSON object: {path}")
    return payload


def atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)


def _require_markers(
    bundle: zipfile.ZipFile,
    files: Mapping[str, Any],
    path: str,
    markers: tuple[str, ...],
    errors: list[str],
) -> None:
    if path not in files:
        return
    content = bundle.read(path).decode("utf-8")
    for marker in markers:
        if marker not in content:
            errors.append(f"Pilot release capability marker is missing in {path}: {marker}")


def inspect_runtime_guard(archive: Path) -> list[str]:
    verification = verify_release(archive)
    errors = [str(item) for item in verification.get("errors", [])]
    if not verification.get("ok"):
        return errors or ["Release archive verification failed"]

    try:
        with zipfile.ZipFile(archive, "r") as bundle:
            manifest = json.loads(bundle.read(MANIFEST_NAME))
            files = manifest.get("files")
            if not isinstance(files, dict):
                return ["Release manifest file map is invalid"]
            missing = sorted(REQUIRED_FILES - set(files))
            if missing:
                errors.append("Release is missing pilot runtime files: " + ", ".join(missing))

            for path, markers in MARKER_REQUIREMENTS:
                _require_markers(bundle, files, path, markers, errors)

            if "docker-compose.production.yml" in files:
                compose = bundle.read("docker-compose.production.yml").decode("utf-8")
                for marker in ("./docs:/app/docs:ro", "./deploy/release:/app/deploy/release:ro"):
                    if marker not in compose:
                        errors.append(f"Production evidence mount is missing: {marker}")
            for script in ("scripts/deploy_production.sh", "scripts/rollback.sh"):
                if script in files:
                    content = bundle.read(script).decode("utf-8")
                    if "pilot_runtime.py _stop" not in content:
                        errors.append(f"{script} does not stop active pilot runtime")
                    if "check_pilot_runtime_integrity.py" not in content:
                        errors.append(f"{script} does not audit pilot runtime database integrity")
            if "scripts/rollback.sh" in files:
                rollback = bundle.read("scripts/rollback.sh").decode("utf-8")
                for marker in (
                    'CAPABILITY_SCRIPT="scripts/pilot_release_capability.py"',
                    '"$CAPABILITY_SCRIPT" inspect --archive',
                    "scripts/verify_backup.sh",
                    "restore_postgres.sh",
                    "docker compose build backend frontend admin bot notification_worker scheduler",
                    "RELEASE_STATE_DIR=",
                    '--state-dir "$RELEASE_STATE_DIR"',
                    "PROMOTED_RELEASE=",
                    "Rollback release pointer promotion mismatch",
                    "verify --slot both",
                    "record-rollback",
                ):
                    if marker not in rollback:
                        errors.append(f"scripts/rollback.sh is missing rollback guard: {marker}")
    except (OSError, KeyError, UnicodeDecodeError, zipfile.BadZipFile, json.JSONDecodeError) as exc:
        errors.append(f"Unable to inspect release runtime capability: {exc}")
    return list(dict.fromkeys(errors))


def capability_payload(state: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "kind": "release_capability",
        "name": CAPABILITY_NAME,
        "version": CAPABILITY_VERSION,
        "archive_sha256": state.get("sha256"),
        "git_commit": state.get("git_commit"),
        "release_id": state.get("release_id"),
    }


def validate_capability(state: Mapping[str, Any], secret: str) -> list[str]:
    errors: list[str] = []
    capabilities = state.get("capabilities")
    capability = capabilities.get(CAPABILITY_NAME) if isinstance(capabilities, Mapping) else None
    if not isinstance(capability, Mapping):
        return [f"Release is missing signed {CAPABILITY_NAME} capability"]
    if not verify_payload_signature(capability, secret):
        errors.append(f"Release {CAPABILITY_NAME} capability signature is invalid")
    expected = capability_payload(state)
    for key, value in expected.items():
        if capability.get(key) != value:
            errors.append(f"Release capability {key} does not match release state")
    return list(dict.fromkeys(errors))


def stamp_slot(slot: str, env_path: Path) -> dict[str, Any]:
    path = STATE_DIR / f"{slot}_release.json"
    state = load_json(path)
    archive = Path(str(state.get("archive", "")))
    if not archive.is_file():
        raise ValueError(f"Release archive is missing: {archive}")
    if sha256_file(archive) != str(state.get("sha256", "")):
        raise ValueError("Release archive SHA-256 does not match release state")
    errors = inspect_runtime_guard(archive)
    if errors:
        raise ValueError("; ".join(errors))
    secret = require_signing_secret(read_env(env_path))
    capabilities = dict(state.get("capabilities") or {})
    capabilities[CAPABILITY_NAME] = sign_payload(capability_payload(state), secret)
    state["capabilities"] = capabilities
    atomic_write_json(path, state)
    return state


def verify_slot(slot: str, env_path: Path, *, inspect_archive: bool = True) -> list[str]:
    path = STATE_DIR / f"{slot}_release.json"
    try:
        state = load_json(path)
        secret = require_signing_secret(read_env(env_path))
    except ValueError as exc:
        return [str(exc)]
    errors = validate_capability(state, secret)
    if inspect_archive:
        archive = Path(str(state.get("archive", "")))
        if not archive.is_file():
            errors.append(f"Release archive is missing: {archive}")
        else:
            if sha256_file(archive) != str(state.get("sha256", "")):
                errors.append("Release archive SHA-256 does not match release state")
            errors.extend(inspect_runtime_guard(archive))
    return list(dict.fromkeys(errors))


def parser() -> argparse.ArgumentParser:
    command = argparse.ArgumentParser(description=__doc__)
    sub = command.add_subparsers(dest="command", required=True)
    stamp = sub.add_parser("stamp", help="Inspect and sign one release pointer capability")
    stamp.add_argument("--slot", choices=("current", "previous"), default="current")
    stamp.add_argument("--env", type=Path, default=ROOT / ".env")
    verify = sub.add_parser("verify", help="Verify signed runtime capabilities")
    verify.add_argument("--slot", choices=("current", "previous", "both"), default="both")
    verify.add_argument("--env", type=Path, default=ROOT / ".env")
    inspect = sub.add_parser("inspect", help="Reject an immutable archive without runtime guard")
    inspect.add_argument("--archive", type=Path, required=True)
    return command


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "stamp":
            state = stamp_slot(args.slot, args.env)
            print(
                json.dumps(
                    {
                        "ok": True,
                        "slot": args.slot,
                        "release_id": state.get("release_id"),
                        "sha256": state.get("sha256"),
                        "capability": CAPABILITY_NAME,
                        "version": CAPABILITY_VERSION,
                    },
                    ensure_ascii=False,
                )
            )
            return 0
        if args.command == "inspect":
            errors = inspect_runtime_guard(args.archive)
            print(
                json.dumps(
                    {
                        "ok": not errors,
                        "archive": str(args.archive.resolve()),
                        "errors": errors,
                    },
                    ensure_ascii=False,
                )
            )
            return 1 if errors else 0
        slots = ("current", "previous") if args.slot == "both" else (args.slot,)
        errors = {slot: verify_slot(slot, args.env) for slot in slots}
        failed = {slot: values for slot, values in errors.items() if values}
        print(json.dumps({"ok": not failed, "slots": errors}, ensure_ascii=False))
        return 1 if failed else 0
    except (OSError, ValueError) as exc:
        print(json.dumps({"ok": False, "errors": [str(exc)]}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
