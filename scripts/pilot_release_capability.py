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
from pilot_readiness import read_env
from release_control import MANIFEST_NAME, sha256_file, verify_release

ROOT = Path(__file__).resolve().parents[1]
STATE_DIR = ROOT / "deploy/release/runtime"
CAPABILITY_NAME = "pilot_runtime_guard"
CAPABILITY_VERSION = 4
REQUIRED_FILES = {
    ".env.production.example",
    ".github/workflows/ci.yml",
    "admin/index.html",
    "admin/src/BusinessEventsPanel.jsx",
    "admin/src/ServiceOperationsPanel.jsx",
    "admin/src/serviceOperations.css",
    "admin/src/serviceOperations.js",
    "admin/src/serviceOperations.test.js",
    "backend/pilot_models.py",
    "backend/services/pilot_runtime.py",
    "backend/services/pilot_circuit_breaker.py",
    "backend/services/payment_reconciliation.py",
    "backend/alembic/versions/0022_pilot_runtime_guard.py",
    "backend/api/orders.py",
    "backend/api/payments.py",
    "backend/api/returns.py",
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
    "e2e/tests/storefront.spec.js",
    "scripts/pilot_runtime.py",
    "scripts/check_pilot_runtime_integrity.py",
    "scripts/pilot_release_capability.py",
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
            _require_markers(bundle, files, "backend/services/pilot_circuit_breaker.py", ("def stop_pilot_for_order(", "def trip_pilot_circuit_breaker("), errors)
            _require_markers(bundle, files, "backend/api/payments.py", ("ProviderPaymentIntegrityError", "trip_pilot_circuit_breaker(", "stop_pilot_for_order("), errors)
            _require_markers(bundle, files, "backend/api/returns.py", ("trip_pilot_circuit_breaker(", "stop_pilot_for_order("), errors)
            _require_markers(bundle, files, "backend/services/payment_reconciliation.py", ("payment_reconciliation_mismatch", "stop_pilot_for_order("), errors)
            _require_markers(bundle, files, "backend/main.py", ("collect_pilot_metrics", '@app.get("/metrics"', "return metrics_response()"), errors)
            _require_markers(bundle, files, "backend/middleware/metrics.py", ("flashin_pilot_metrics_collection_success", "def collect_pilot_metrics(", 'return "__unmatched__"'), errors)
            _require_markers(bundle, files, "deploy/monitoring/rules/flashin_pilot.yml", ("FlashinPilotMetricsUnavailable", "FlashinPilotArtifactIntegrityFailed", "FlashinPilotMoneyAttentionRequired", "FlashinPilotCapacityLow"), errors)
            _require_markers(bundle, files, "deploy/grafana/dashboards/flashin_operations.json", ("FLASHIN Operations", "flashin_pilot_checkout_ready", "flashin_pilot_money_attention"), errors)
            _require_markers(bundle, files, "deploy/grafana/provisioning/datasources/prometheus.yml", ("prometheus", "http://prometheus:9090"), errors)
            _require_markers(bundle, files, "deploy/monitoring/prometheus.yml", ("rule_files", "/etc/prometheus/rules/*.yml", "backend:8000"), errors)
            _require_markers(bundle, files, "scripts/check_production_compose.py", ('MONITORING_SERVICES = {"prometheus", "grafana"}', 'PRODUCTION_PROFILES = ("production", "workers", "scheduler", "search", "monitoring")', "Grafana anonymous access must be disabled"), errors)
            _require_markers(bundle, files, ".env.production.example", ("METRICS_ENABLED=true", "GRAFANA_ADMIN_USER=", "GRAFANA_ADMIN_PASSWORD="), errors)
            _require_markers(bundle, files, "docker-compose.yml", ("prometheus:", "grafana:", "prometheus_data", "grafana_data"), errors)
            _require_markers(bundle, files, ".github/workflows/ci.yml", ("browser-e2e:", "Install Chromium", "Run Mini App and Admin browser journeys", "needs: [backend, frontend, admin, browser-e2e]"), errors)
            _require_markers(bundle, files, "e2e/package.json", ('"@playwright/test": "1.54.2"', '"test": "playwright test"'), errors)
            _require_markers(bundle, files, "e2e/playwright.config.js", ('name: "storefront-mobile"', 'name: "admin-desktop"', 'trace: "retain-on-failure"', 'screenshot: "only-on-failure"', 'video: "retain-on-failure"'), errors)
            _require_markers(bundle, files, "e2e/tests/storefront.spec.js", ("Mini App critical pilot journey", "Mini App cart quantity and removal controls", "Mini App profile, support, privacy and return journey", "Mini App payment return route refreshes paid order"), errors)
            _require_markers(bundle, files, "e2e/tests/admin.spec.js", ("Admin critical pilot operator journey", "Admin operations, fulfillment and BusinessEvent recovery journey", "Admin completes support, privacy and refund service operations"), errors)
            _require_markers(bundle, files, "admin/src/ServiceOperationsPanel.jsx", ('support: "/api/support/admin/tickets"', 'privacy: "/api/privacy/admin/requests"', 'returns: "/api/admin/returns"', 'adminJson("/api/returns/admin/approve"', "Подтвердить refund"), errors)
            _require_markers(bundle, files, "admin/src/serviceOperations.js", ("export function supportTransitions(", "export function canProcessPrivacy(", "export function canApproveReturn(", "export function normalizeRefundAmount(", "export function serviceAttentionCount("), errors)
            _require_markers(bundle, files, "admin/src/serviceOperations.test.js", ("support transitions follow the backend state machine", "refund amount is positive, bounded and rounded", "aggregate attention are fail-closed"), errors)
            _require_markers(bundle, files, "admin/src/BusinessEventsPanel.jsx", ('import ServiceOperationsPanel from "./ServiceOperationsPanel.jsx"', "<ServiceOperationsPanel onUnauthorized={onUnauthorized} />"), errors)
            _require_markers(bundle, files, "admin/index.html", ('href="/src/serviceOperations.css"', "FLASHIN Admin"), errors)
            _require_markers(bundle, files, "admin/src/serviceOperations.css", (".service-operations", ".service-grid", ".attention-badge"), errors)
            _require_markers(bundle, files, "docs/pilot/end_to_end_coverage_matrix.md", ("## Browser journeys", "Seven stateful Playwright journeys", "Admin service operations", "## Evidence boundary"), errors)

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
                for marker in ('CAPABILITY_SCRIPT="scripts/pilot_release_capability.py"', '"$CAPABILITY_SCRIPT" inspect --archive'):
                    if marker not in rollback:
                        errors.append("scripts/rollback.sh does not reject unguarded target archives")
                        break
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
