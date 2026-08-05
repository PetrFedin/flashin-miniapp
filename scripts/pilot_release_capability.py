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
    "scripts/pilot_control_chain.py",
    "scripts/pilot_control_lock.py",
    "scripts/pilot_control_io.py",
    "scripts/pilot_control.py",
    "backend/pilot_models.py",
    "backend/alembic/versions/0023_pilot_state_replay_anchor.py",
    "scripts/pilot_runner.py",
    "backend/tests/test_pilot_control_binding.py",
    "backend/tests/test_pilot_control_signature.py",
    "backend/tests/test_pilot_control_durability.py",
    "backend/tests/test_pilot_runtime.py",
    "backend/tests/test_pilot_state_replay_migration.py",
    "Makefile",
    "scripts/pilot_runtime.py",
    "scripts/check_pilot_runtime_integrity.py",
    "scripts/pilot_release_capability.py",
    "scripts/pilot_release_contract.py",
    "scripts/check_production_compose.py",
    "docker-compose.yml",
    "docker-compose.production.yml",
    "scripts/deploy_production.sh",
    "scripts/rollback.sh",
}


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

            _require_markers(bundle, files, "backend/api/orders.py", ("acquire_pilot_checkout(", "record_pilot_order("), errors)
            _require_markers(bundle, files, "scripts/pilot_release_contract.py", ("CAPABILITY_VERSION = 14",), errors)
            _require_markers(bundle, files, "scripts/pilot_release_capability.py", ("from pilot_release_contract import CAPABILITY_VERSION",), errors)
            _require_markers(bundle, files, "backend/services/pilot_runtime.py", ("from scripts.pilot_release_contract import CAPABILITY_VERSION", '"version": CAPABILITY_VERSION'), errors)
            _require_markers(bundle, files, "scripts/readiness_gate.py", ("def build_signed_live_report(", '"kind": "pilot_live_gate"', "configuration_fingerprint(env, secret)", "release_binding(current_release)", "return sign_payload(payload, secret)"), errors)
            _require_markers(bundle, files, "scripts/pilot_admission.py", ("live gate evidence signature is invalid", "live gate configuration fingerprint does not match", "live gate release binding is missing", "validate_release_binding(release, current_release)", "def validate_admission_evidence_inputs(", "current_release=current_release"), errors)
            _require_markers(bundle, files, "backend/tests/test_pilot_admission.py", ("test_live_gate_rejects_tampering_configuration_and_other_release", "test_admission_create_preflight_binds_live_gate_to_current_release", "configuration fingerprint", "live gate release"), errors)
            _require_markers(bundle, files, "scripts/pilot_control_binding.py", ("def build_admission_binding(", "manifest_sha256", "def validate_admission_binding(", "def require_admission_binding("), errors)
            _require_markers(bundle, files, "scripts/pilot_control.py", ("SCHEMA_VERSION = 4", "durable_atomic_write_text(", "def refresh_summary(", "derived and non-authoritative", "return _report_exit(report, final=args.final)"), errors)
            _require_markers(bundle, files, "scripts/pilot_runner.py", ("errors = verify_default_admission(ROOT)", "return pilot_control_main(args)"), errors)
            _require_markers(bundle, files, "backend/services/pilot_runtime.py", ("build_admission_binding(manifest_path, manifest)", "validate_state_descendant(", "validated_anchor.update(state_anchor(pilot_state))", "state.pilot_state_revision", "state.pilot_state_sha256", "armed runtime pilot state replay anchor is missing"), errors)
            _require_markers(bundle, files, "scripts/pilot_runtime.py", ("build_admission_binding(DEFAULT_MANIFEST, manifest)", "pilot_state_revision", "pilot_state_sha256", "pilot_state_history", "validate_anchor_transition(", "Stopped pilot runtime cannot change admission or release lineage"), errors)
            _require_markers(bundle, files, "Makefile", ("python3 scripts/pilot_runner.py init", "python3 scripts/pilot_runner.py record $(ARGS)", "python3 scripts/pilot_runner.py status", "python3 scripts/pilot_runner.py validate --final"), errors)
            _require_markers(bundle, files, "backend/tests/test_pilot_control_binding.py", ("test_state_is_bound_to_one_exact_signed_admission_file", "test_legacy_state_is_rejected_without_silent_migration", "test_makefile_routes_pilot_control_through_admission_runner"), errors)
            _require_markers(bundle, files, "scripts/pilot_control_chain.py", ("def signed_state_sha256(", "def validate_anchor_transition(", "pilot control state revision rollback detected", "pilot control state ancestry does not match the armed runtime"), errors)
            _require_markers(bundle, files, "scripts/pilot_control_lock.py", ("def exclusive_state_lock(", "fcntl.LOCK_EX | fcntl.LOCK_NB", "Pilot control state lock acquisition timed out", "os.fchmod(handle.fileno(), 0o600)"), errors)
            _require_markers(bundle, files, "scripts/pilot_control_io.py", ("def durable_atomic_write_text(", "os.fsync(handle.fileno())", "os.replace(temporary_path, path)", "_fsync_directory(path.parent)", "os.fchmod(handle.fileno(), 0o600)"), errors)
            _require_markers(bundle, files, "backend/pilot_models.py", ("pilot_state_revision", "pilot_state_sha256", "ck_pilot_runtime_state_anchor"), errors)
            _require_markers(bundle, files, "backend/alembic/versions/0023_pilot_state_replay_anchor.py", ("0023_pilot_state_replay_anchor", "0022_pilot_runtime_guard", "pilot_state_revision", "pilot_state_sha256"), errors)
            _require_markers(bundle, files, "backend/tests/test_pilot_control_signature.py", ("test_cross_process_writers_serialize_and_reject_stale_parent", "test_cross_process_lock_timeout_fails_closed", "multiprocessing.get_context(\"fork\")"), errors)
            _require_markers(bundle, files, "backend/tests/test_pilot_control_durability.py", ("test_durable_atomic_write_fsyncs_file_and_parent_directory", "test_summary_refresh_repairs_stale_file_without_advancing_state", "test_summary_write_failure_leaves_valid_committed_state_and_is_repairable", "test_status_summary_refresh_does_not_change_signed_json_bytes"), errors)
            _require_markers(bundle, files, "backend/tests/test_pilot_runtime.py", ("test_tampered_pilot_control_state_fails_closed_on_checkout", "test_runtime_anchor_advances_to_descendant_and_rejects_replay", "test_unrelated_valid_signed_state_branch_fails_closed"), errors)
            _require_markers(bundle, files, "backend/services/pilot_circuit_breaker.py", ("def stop_pilot_for_order(", "def trip_pilot_circuit_breaker("), errors)
            _require_markers(bundle, files, "backend/api/payments.py", ("ProviderPaymentIntegrityError", "trip_pilot_circuit_breaker(", "stop_pilot_for_order("), errors)
            _require_markers(bundle, files, "backend/api/returns.py", ("trip_pilot_circuit_breaker(", "stop_pilot_for_order("), errors)
            _require_markers(bundle, files, "backend/api/support.py", ("class AdminSupportTicketOut", "assigned_admin_id: int | None = None", "response_model=list[AdminSupportTicketOut]", "response_model=AdminSupportTicketOut"), errors)
            _require_markers(bundle, files, "backend/tests/test_support_admin_schema.py", ("test_admin_support_ticket_schema_exposes_accountable_owner", "assigned_admin_id"), errors)
            _require_markers(bundle, files, "backend/services/payment_reconciliation.py", ("payment_reconciliation_mismatch", "stop_pilot_for_order("), errors)
            _require_markers(bundle, files, "backend/order_statuses.py", ("SETTLED_ORDER_PAYMENT_STATUSES", '"paid_review_required"', '"refund_review_required"'), errors)
            _require_markers(bundle, files, "backend/services/payment_settlement.py", ("from ..order_statuses import SETTLED_ORDER_PAYMENT_STATUSES", "reward_referral_after_first_paid_order(db, order.customer_id, order.id)", "if order.payment_status in SETTLED_ORDER_PAYMENT_STATUSES"), errors)
            _require_markers(bundle, files, "backend/services/loyalty.py", ("def _lock_referral_customer(", "def _has_prior_settled_order(", "Referral code must be applied before the first paid order", "return attach_referral_to_customer(db, code, new_customer_id)", 'attribution.status = "ineligible"', "def reward_referral_after_first_paid_order("), errors)
            _require_markers(bundle, files, "backend/tests/test_referral_attribution.py", ("test_legacy_apply_referral_only_attaches_pending_attribution", "test_referral_after_settled_order_is_rejected_even_for_same_code", "test_missing_customer_is_not_silently_eligible"), errors)
            _require_markers(bundle, files, "backend/services/fulfillment.py", ("def _picklist_is_complete(", "Every picklist item must be fully picked before packing", 'order.delivery_status = "ready"'), errors)
            _require_markers(bundle, files, "backend/api/fulfillment.py", ("fulfillment.task.update", "fulfillment.task_item.update", "assigned_admin_id"), errors)
            _require_markers(bundle, files, "backend/services/delivery_providers.py", ("_SHIPMENT_TRANSITIONS", "Only a ready order can be transferred to delivery", 'order.status = "shipped"', 'order.status = "completed"'), errors)
            _require_markers(bundle, files, "backend/api/delivery_providers.py", ("delivery.shipment.create", "delivery.shipment.update", "with_for_update()"), errors)
            _require_markers(bundle, files, "backend/main.py", ("collect_pilot_metrics", '@app.get("/metrics"', "return metrics_response()"), errors)
            _require_markers(bundle, files, "backend/middleware/metrics.py", ("flashin_pilot_metrics_collection_success", "def collect_pilot_metrics(", 'return "__unmatched__"'), errors)
            _require_markers(bundle, files, "deploy/monitoring/rules/flashin_pilot.yml", ("FlashinPilotMetricsUnavailable", "FlashinPilotArtifactIntegrityFailed", "FlashinPilotMoneyAttentionRequired", "FlashinPilotCapacityLow"), errors)
            _require_markers(bundle, files, "deploy/grafana/dashboards/flashin_operations.json", ("FLASHIN Operations", "flashin_pilot_checkout_ready", "flashin_pilot_money_attention"), errors)
            _require_markers(bundle, files, "deploy/grafana/provisioning/datasources/prometheus.yml", ("prometheus", "http://prometheus:9090"), errors)
            _require_markers(bundle, files, "deploy/monitoring/prometheus.yml", ("rule_files", "/etc/prometheus/rules/*.yml", "backend:8000"), errors)
            _require_markers(bundle, files, "scripts/check_production_compose.py", ('MONITORING_SERVICES = {"prometheus", "grafana"}', 'PRODUCTION_PROFILES = ("production", "workers", "scheduler", "search", "monitoring")', "Grafana anonymous access must be disabled"), errors)
            _require_markers(bundle, files, ".env.production.example", ("METRICS_ENABLED=true", "GRAFANA_ADMIN_USER=", "GRAFANA_ADMIN_PASSWORD="), errors)
            _require_markers(bundle, files, "docker-compose.yml", ("prometheus:", "grafana:", "prometheus_data", "grafana_data"), errors)
            _require_markers(bundle, files, ".github/workflows/ci.yml", ("browser-e2e:", "Install Chromium", "Run Mini App and Admin browser journeys", "Run transactional referral attribution smoke", "Run transactional full fulfillment smoke", "Run signed backup and restore drill", "bash scripts/backup_restore_smoke.sh", "Run signed full release rollback drill", "bash scripts/release_rollback_smoke.sh", "needs: [backend, frontend, admin, browser-e2e]"), errors)
            _require_markers(bundle, files, "e2e/package.json", ('"@playwright/test": "1.54.2"', '"test": "playwright test"'), errors)
            _require_markers(bundle, files, "e2e/playwright.config.js", ('name: "storefront-mobile"', 'name: "admin-desktop"', 'trace: "retain-on-failure"', 'screenshot: "only-on-failure"', 'video: "retain-on-failure"'), errors)
            _require_markers(bundle, files, "e2e/tests/storefront.spec.js", ("Mini App critical pilot journey", "Mini App cart quantity and removal controls", "Mini App profile, support, privacy and return journey", "Mini App payment return route refreshes paid order"), errors)
            _require_markers(bundle, files, "e2e/tests/admin.spec.js", ("Admin critical pilot operator journey", "Admin operations, fulfillment and BusinessEvent recovery journey", "Admin completes support, privacy and refund service operations"), errors)
            _require_markers(bundle, files, "e2e/tests/owner-admin.spec.js", ("Admin assigns an accountable owner to a support ticket", "assigned_admin_id: 42", "Ответственный обращения 901"), errors)
            _require_markers(bundle, files, "e2e/tests/fulfillment-admin.spec.js", ("Admin completes picklist, shipment and delivery lifecycle", "Собрать все позиции и упаковать", "PILOT-TRACK-9100", 'status: "completed"'), errors)
            _require_markers(bundle, files, "admin/src/FulfillmentOperationsPanel.jsx", ('"/api/fulfillment/tasks"', '"/api/delivery-providers/shipments"', "async function pickAndPack(", "async function ship(", "async function deliver("), errors)
            _require_markers(bundle, files, "admin/src/fulfillmentOperations.js", ("export function isPicklistComplete(", "export function fulfillmentAction(", "export function normalizeTracking(", "export function fulfillmentAttentionCount(", "Собрать все позиции и упаковать", "Передать в доставку", "Подтвердить доставку"), errors)
            _require_markers(bundle, files, "admin/src/fulfillmentOperations.test.js", ("fulfillment actions expose only the next safe workflow step", "picklist completeness requires every ordered unit", "tracking is bounded and meaningful", "attention remains until shipment is delivered"), errors)
            _require_markers(bundle, files, "admin/src/ServiceOperationsPanel.jsx", ('support: "/api/support/admin/tickets"', 'privacy: "/api/privacy/admin/requests"', 'returns: "/api/admin/returns"', 'adminJson("/api/returns/admin/approve"', "Подтвердить refund", "Ответственный обращения"), errors)
            _require_markers(bundle, files, "admin/src/serviceOperations.js", ("export function supportTransitions(", "export function canProcessPrivacy(", "export function canApproveReturn(", "export function normalizeAdminAssignment(", "export function normalizeRefundAmount(", "export function serviceAttentionCount("), errors)
            _require_markers(bundle, files, "admin/src/serviceOperations.test.js", ("support transitions follow the backend state machine", "support owner assignment accepts only positive integer Admin IDs", "refund amount is positive, bounded and rounded", "aggregate attention are fail-closed"), errors)
            _require_markers(bundle, files, "admin/src/BusinessEventsPanel.jsx", ('import FulfillmentOperationsPanel from "./FulfillmentOperationsPanel.jsx"', '<FulfillmentOperationsPanel onUnauthorized={onUnauthorized} />', 'import ServiceOperationsPanel from "./ServiceOperationsPanel.jsx"', "<ServiceOperationsPanel onUnauthorized={onUnauthorized} />"), errors)
            _require_markers(bundle, files, "admin/index.html", ('href="/src/serviceOperations.css"', "FLASHIN Admin"), errors)
            _require_markers(bundle, files, "admin/src/serviceOperations.css", (".service-operations", ".service-grid", ".attention-badge"), errors)
            _require_markers(bundle, files, "scripts/full_fulfillment_smoke.py", ("Every picklist item must be fully picked before packing", "idempotent shipment create", 'persisted_order.status == "completed"', 'persisted_order.delivery_status == "delivered"'), errors)
            _require_markers(bundle, files, "scripts/referral_attribution_smoke.py", ("duplicate referral payment webhook", "late_referral.status_code == 409", "persisted_referral.used_count == 1", "len(reward_rows) == 1", "second_persisted_order.referral_code is None"), errors)
            _require_markers(bundle, files, "scripts/backup_integrity.py", ('KIND = "postgres_backup_manifest"', "CRITICAL_TABLES = (", "def snapshot_database(", "def verify_restorable(", "def verify_live_database(", "backup SHA-256 does not match signed manifest", "restored critical table"), errors)
            _require_markers(bundle, files, "scripts/backup_postgres.sh", ("MANIFEST_FILE=", 'python3 "$INTEGRITY_SCRIPT" create', "Backup created, restored in isolation and signed"), errors)
            _require_markers(bundle, files, "scripts/verify_backup.sh", ("Signed backup manifest not found", 'python3 "$INTEGRITY_SCRIPT" verify', "Backup signature, archive, schema and critical data verification OK"), errors)
            _require_markers(bundle, files, "scripts/restore_postgres.sh", ("Signed backup manifest not found", 'python3 "$INTEGRITY_SCRIPT" verify', 'python3 "$INTEGRITY_SCRIPT" verify-live', "signed snapshot verified"), errors)
            _require_markers(bundle, files, "scripts/backup_restore_smoke.sh", ("tampered_archive_rejected", "mutated_database_rejected", "restored_value_verified", "verify-live", "restore_postgres.sh --yes"), errors)
            _require_markers(bundle, files, "scripts/release_rollback_smoke.sh", ("ROLLBACK_DRILL=1", "PREVIOUS_MARKER=", "CURRENT_MARKER=", "container_marker=", "restored_name=", "verify-live", "verify --slot both", "verify-rollback", "runtime_image_rebuilt", "release_pointer_promoted", "signed_evidence_verified"), errors)
            _require_markers(bundle, files, "backend/tests/test_backup_integrity.py", ("test_signed_manifest_binds_exact_archive_and_snapshot", "test_archive_byte_or_size_change_is_rejected", "test_snapshot_comparison_detects_schema_revision_and_ledger_changes", "test_database_identifiers_fail_closed"), errors)
            _require_markers(bundle, files, "docs/pilot/end_to_end_coverage_matrix.md", ("## Browser journeys", "Nine stateful Playwright journeys", "accountable active Admin ID", "Admin service operations", "full picklist", "## Transactional referral evidence", "first paid order -> one inviter reward", "## Signed backup and restore evidence", "Backup/restore integrity", "Release rollback", "## Evidence boundary"), errors)

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
            print(json.dumps({"ok": True, "slot": args.slot, "release_id": state.get("release_id"), "sha256": state.get("sha256"), "capability": CAPABILITY_NAME, "version": CAPABILITY_VERSION}, ensure_ascii=False))
            return 0
        if args.command == "inspect":
            errors = inspect_runtime_guard(args.archive)
            print(json.dumps({"ok": not errors, "archive": str(args.archive.resolve()), "errors": errors}, ensure_ascii=False))
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
