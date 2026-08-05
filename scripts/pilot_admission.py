#!/usr/bin/env python3
"""Create and verify the signed FLASHIN pilot admission manifest."""

from __future__ import annotations

import argparse
import json
from datetime import timedelta
from pathlib import Path
from typing import Any, Mapping

from pilot_evidence import (
    atomic_write_json,
    atomic_write_text,
    configuration_fingerprint,
    load_json,
    parse_timestamp,
    release_binding,
    require_signing_secret,
    sha256_file,
    sign_payload,
    utc_now,
    utc_timestamp,
    validate_evidence_window,
    validate_provider_report,
    validate_release_binding,
    validate_rollback_drill_report,
    verify_payload_signature,
)
from pilot_readiness import read_env
from release_control import verify_release

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / "docs/pilot/pilot_admission_manifest.json"
DEFAULT_PROVIDER_REPORT = ROOT / "docs/pilot/integration_check_report.json"
DEFAULT_LIVE_REPORT = ROOT / "docs/pilot_live_gate_report.json"
DEFAULT_ROLLBACK_REPORT = ROOT / "docs/pilot/rollback_drill_report.json"
CURRENT_RELEASE_PATH = ROOT / "deploy/release/runtime/current_release.json"
PREVIOUS_RELEASE_PATH = ROOT / "deploy/release/runtime/previous_release.json"
REQUIRED_APPROVALS = (
    "business_owner",
    "operations_owner",
    "technical_owner",
    "legal_owner",
    "support_owner",
)
REQUIRED_ACKNOWLEDGEMENTS = (
    "legal_documents_approved",
    "support_process_ready",
    "rollback_drill_completed",
    "provider_probe_side_effect_understood",
    "pilot_scope_limited_to_20_orders",
)


def _positive_int(env: Mapping[str, str], key: str, default: int, minimum: int, maximum: int) -> int:
    raw = str(env.get(key, default)).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{key} must be an integer") from exc
    if value < minimum or value > maximum:
        raise ValueError(f"{key} must be between {minimum} and {maximum}")
    return value


def load_verified_release_state(path: Path) -> dict[str, Any]:
    state = load_json(path)
    archive = Path(str(state.get("archive", "")))
    verification = verify_release(archive)
    if not verification.get("ok"):
        raise ValueError(
            f"Release pointer is invalid ({path.name}): "
            + "; ".join(str(item) for item in verification.get("errors", []))
        )
    for key in ("release_id", "git_commit", "sha256"):
        if str(state.get(key, "")) != str(verification.get(key, "")):
            raise ValueError(f"Release pointer {path.name} {key} does not match archive")
    return state


def validate_live_gate_report(
    report: Mapping[str, Any],
    *,
    env: Mapping[str, str],
    current_release: Mapping[str, Any],
    max_age_minutes: int,
) -> list[str]:
    errors: list[str] = []
    try:
        secret = require_signing_secret(env)
    except ValueError as exc:
        return [str(exc)]
    if report.get("schema_version") != 1:
        errors.append("unsupported live gate evidence schema")
    if report.get("kind") != "pilot_live_gate":
        errors.append("live gate evidence kind is invalid")
    if not verify_payload_signature(report, secret):
        errors.append("live gate evidence signature is invalid")
    if report.get("configuration_fingerprint") != configuration_fingerprint(env, secret):
        errors.append("live gate configuration fingerprint does not match")
    release = report.get("release")
    if not isinstance(release, Mapping):
        errors.append("live gate release binding is missing")
    else:
        errors.extend(
            f"live gate release: {item}"
            for item in validate_release_binding(release, current_release)
        )
    if report.get("phase") != "live":
        errors.append("live gate phase is not live")
    if report.get("go") is not True:
        errors.append("live gate decision is not GO")
    summary = report.get("summary")
    if not isinstance(summary, Mapping) or summary.get("critical_failed") != 0:
        errors.append("live gate has critical failures")
    checks = report.get("checks")
    if not isinstance(checks, list):
        errors.append("live gate checks are missing")
    else:
        provider_checks = [
            item
            for item in checks
            if isinstance(item, Mapping)
            and item.get("name") == "live:provider_integrations"
        ]
        if len(provider_checks) != 1 or provider_checks[0].get("ok") is not True:
            errors.append("live gate did not verify provider evidence")
    errors.extend(
        validate_evidence_window(
            report,
            now=None,
            maximum_age=timedelta(minutes=max_age_minutes),
        )
    )
    return list(dict.fromkeys(errors))


def build_manifest(
    *,
    env: Mapping[str, str],
    current_release: Mapping[str, Any],
    previous_release: Mapping[str, Any],
    provider_report_path: Path,
    live_report_path: Path,
    rollback_report_path: Path,
    approvals: Mapping[str, str],
    acknowledgements: Mapping[str, bool],
    max_age_minutes: int,
) -> dict[str, Any]:
    secret = require_signing_secret(env)
    created = utc_now()
    payload: dict[str, Any] = {
        "schema_version": 1,
        "kind": "pilot_admission",
        "created_at": utc_timestamp(created),
        "expires_at": utc_timestamp(created + timedelta(minutes=max_age_minutes)),
        "decision": "GO",
        "configuration_fingerprint": configuration_fingerprint(env, secret),
        "release": release_binding(current_release),
        "previous_release": release_binding(previous_release),
        "evidence": {
            "provider_report": {
                "path": str(provider_report_path.resolve()),
                "sha256": sha256_file(provider_report_path),
            },
            "live_gate_report": {
                "path": str(live_report_path.resolve()),
                "sha256": sha256_file(live_report_path),
            },
            "rollback_drill_report": {
                "path": str(rollback_report_path.resolve()),
                "sha256": sha256_file(rollback_report_path),
            },
        },
        "approvals": dict(approvals),
        "acknowledgements": dict(acknowledgements),
        "pilot_contract": {
            "maximum_orders": 20,
            "automatic_stop_on_critical_failure": True,
            "mass_admission_forbidden": True,
        },
    }
    return sign_payload(payload, secret)


def _validate_evidence_hash(entry: Any, label: str) -> tuple[Path | None, list[str]]:
    errors: list[str] = []
    if not isinstance(entry, Mapping):
        return None, [f"{label} evidence entry is missing"]
    path = Path(str(entry.get("path", "")))
    if not path.is_file():
        return path, [f"{label} evidence file is missing"]
    if entry.get("sha256") != sha256_file(path):
        errors.append(f"{label} evidence checksum does not match")
    return path, errors


def validate_admission_manifest(
    manifest: Mapping[str, Any],
    *,
    env: Mapping[str, str],
    current_release: Mapping[str, Any],
    previous_release: Mapping[str, Any],
    admission_max_age_minutes: int,
    provider_max_age_minutes: int,
    live_max_age_minutes: int,
    rollback_max_age_days: int,
) -> list[str]:
    errors: list[str] = []
    try:
        secret = require_signing_secret(env)
    except ValueError as exc:
        return [str(exc)]
    if manifest.get("schema_version") != 1:
        errors.append("unsupported pilot admission schema")
    if manifest.get("kind") != "pilot_admission":
        errors.append("manifest kind must be pilot_admission")
    if manifest.get("decision") != "GO":
        errors.append("pilot admission decision is not GO")
    if not verify_payload_signature(manifest, secret):
        errors.append("pilot admission signature is invalid")
    if manifest.get("configuration_fingerprint") != configuration_fingerprint(env, secret):
        errors.append("pilot admission configuration fingerprint does not match")
    errors.extend(
        validate_evidence_window(
            manifest,
            now=None,
            maximum_age=timedelta(minutes=admission_max_age_minutes),
        )
    )

    release = manifest.get("release")
    previous = manifest.get("previous_release")
    if not isinstance(release, Mapping):
        errors.append("pilot admission release binding is missing")
    else:
        errors.extend(validate_release_binding(release, current_release))
    if not isinstance(previous, Mapping):
        errors.append("pilot admission previous release binding is missing")
    else:
        errors.extend(validate_release_binding(previous, previous_release))
    if current_release.get("sha256") == previous_release.get("sha256"):
        errors.append("current and previous releases must be different")

    approvals = manifest.get("approvals")
    if not isinstance(approvals, Mapping):
        errors.append("pilot admission approvals are missing")
    else:
        for key in REQUIRED_APPROVALS:
            if not str(approvals.get(key, "")).strip():
                errors.append(f"pilot admission approval is missing: {key}")
    acknowledgements = manifest.get("acknowledgements")
    if not isinstance(acknowledgements, Mapping):
        errors.append("pilot admission acknowledgements are missing")
    else:
        for key in REQUIRED_ACKNOWLEDGEMENTS:
            if acknowledgements.get(key) is not True:
                errors.append(f"pilot admission acknowledgement is missing: {key}")

    evidence = manifest.get("evidence")
    if not isinstance(evidence, Mapping):
        errors.append("pilot admission evidence map is missing")
        return list(dict.fromkeys(errors))

    provider_path, provider_hash_errors = _validate_evidence_hash(
        evidence.get("provider_report"), "provider report"
    )
    live_path, live_hash_errors = _validate_evidence_hash(
        evidence.get("live_gate_report"), "live gate report"
    )
    rollback_path, rollback_hash_errors = _validate_evidence_hash(
        evidence.get("rollback_drill_report"), "rollback drill report"
    )
    errors.extend(provider_hash_errors + live_hash_errors + rollback_hash_errors)

    if provider_path and provider_path.is_file():
        errors.extend(
            validate_provider_report(
                load_json(provider_path),
                env=env,
                current_release=current_release,
                max_age_minutes=provider_max_age_minutes,
            )
        )
    if live_path and live_path.is_file():
        errors.extend(
            validate_live_gate_report(
                load_json(live_path),
                env=env,
                current_release=current_release,
                max_age_minutes=live_max_age_minutes,
            )
        )
    if rollback_path and rollback_path.is_file():
        rollback_report = load_json(rollback_path)
        errors.extend(
            validate_rollback_drill_report(
                rollback_report,
                env=env,
                max_age_days=rollback_max_age_days,
            )
        )
        rollback_from = rollback_report.get("from_release")
        rollback_to = rollback_report.get("to_release")
        if not isinstance(rollback_from, Mapping):
            errors.append("rollback drill origin release is missing")
        else:
            errors.extend(
                f"rollback drill origin: {item}"
                for item in validate_release_binding(rollback_from, current_release)
            )
        if not isinstance(rollback_to, Mapping):
            errors.append("rollback drill target release is missing")
        else:
            errors.extend(
                f"rollback drill target: {item}"
                for item in validate_release_binding(rollback_to, previous_release)
            )
    return list(dict.fromkeys(errors))


def render_markdown(manifest: Mapping[str, Any]) -> str:
    release = manifest.get("release") if isinstance(manifest.get("release"), Mapping) else {}
    approvals = manifest.get("approvals") if isinstance(manifest.get("approvals"), Mapping) else {}
    lines = [
        "# FLASHIN pilot admission manifest",
        "",
        f"Decision: **{manifest.get('decision', 'NO-GO')}**",
        "",
        f"Created: `{manifest.get('created_at')}`",
        f"Expires: `{manifest.get('expires_at')}`",
        f"Release: `{release.get('release_id', 'unknown')}` / `{release.get('git_commit', 'unknown')}`",
        "",
        "## Approvals",
        "",
    ]
    for key in REQUIRED_APPROVALS:
        lines.append(f"- `{key}`: {approvals.get(key, 'missing')}")
    lines.extend(
        [
            "",
            "Admission applies only to the controlled first-20-order pilot. It is not approval for mass launch.",
            "",
        ]
    )
    return "\n".join(lines)


def _settings(env: Mapping[str, str]) -> dict[str, int]:
    return {
        "admission": _positive_int(env, "PILOT_ADMISSION_MAX_AGE_MINUTES", 60, 5, 240),
        "provider": _positive_int(env, "PILOT_PROVIDER_EVIDENCE_MAX_AGE_MINUTES", 60, 5, 240),
        "live": _positive_int(env, "PILOT_LIVE_GATE_MAX_AGE_MINUTES", 30, 5, 120),
        "rollback": _positive_int(env, "PILOT_ROLLBACK_DRILL_MAX_AGE_DAYS", 30, 1, 90),
    }


def verify_default_admission(root: Path = ROOT) -> list[str]:
    env = read_env(root / ".env")
    try:
        settings = _settings(env)
        current = load_verified_release_state(root / "deploy/release/runtime/current_release.json")
        previous = load_verified_release_state(root / "deploy/release/runtime/previous_release.json")
        manifest = load_json(root / "docs/pilot/pilot_admission_manifest.json")
    except ValueError as exc:
        return [str(exc)]
    return validate_admission_manifest(
        manifest,
        env=env,
        current_release=current,
        previous_release=previous,
        admission_max_age_minutes=settings["admission"],
        provider_max_age_minutes=settings["provider"],
        live_max_age_minutes=settings["live"],
        rollback_max_age_days=settings["rollback"],
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    create = sub.add_parser("create", help="Create a signed pilot admission manifest")
    create.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    create.add_argument("--provider-report", type=Path, default=DEFAULT_PROVIDER_REPORT)
    create.add_argument("--live-report", type=Path, default=DEFAULT_LIVE_REPORT)
    create.add_argument("--rollback-report", type=Path, default=DEFAULT_ROLLBACK_REPORT)
    create.add_argument("--business-owner", required=True)
    create.add_argument("--operations-owner", required=True)
    create.add_argument("--technical-owner", required=True)
    create.add_argument("--legal-owner", required=True)
    create.add_argument("--support-owner", required=True)
    create.add_argument("--legal-documents-approved", action="store_true")
    create.add_argument("--support-process-ready", action="store_true")
    create.add_argument("--rollback-drill-completed", action="store_true")
    create.add_argument("--provider-probe-side-effect-understood", action="store_true")
    create.add_argument("--pilot-scope-limited-to-20-orders", action="store_true")

    verify = sub.add_parser("verify", help="Verify the current pilot admission manifest")
    verify.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    env = read_env(ROOT / ".env")
    try:
        settings = _settings(env)
        current = load_verified_release_state(CURRENT_RELEASE_PATH)
        previous = load_verified_release_state(PREVIOUS_RELEASE_PATH)
        if current.get("sha256") == previous.get("sha256"):
            raise ValueError("Pilot admission requires two different promoted releases")

        if args.command == "verify":
            manifest = load_json(args.manifest)
            errors = validate_admission_manifest(
                manifest,
                env=env,
                current_release=current,
                previous_release=previous,
                admission_max_age_minutes=settings["admission"],
                provider_max_age_minutes=settings["provider"],
                live_max_age_minutes=settings["live"],
                rollback_max_age_days=settings["rollback"],
            )
            print(json.dumps({"go": not errors, "errors": errors}, ensure_ascii=False))
            return 0 if not errors else 1

        approvals = {
            "business_owner": args.business_owner.strip(),
            "operations_owner": args.operations_owner.strip(),
            "technical_owner": args.technical_owner.strip(),
            "legal_owner": args.legal_owner.strip(),
            "support_owner": args.support_owner.strip(),
        }
        acknowledgements = {
            "legal_documents_approved": args.legal_documents_approved,
            "support_process_ready": args.support_process_ready,
            "rollback_drill_completed": args.rollback_drill_completed,
            "provider_probe_side_effect_understood": args.provider_probe_side_effect_understood,
            "pilot_scope_limited_to_20_orders": args.pilot_scope_limited_to_20_orders,
        }
        missing = [key for key, value in approvals.items() if not value]
        missing.extend(key for key, value in acknowledgements.items() if value is not True)
        if missing:
            raise ValueError("Missing pilot approvals/acknowledgements: " + ", ".join(missing))

        provider = load_json(args.provider_report)
        live = load_json(args.live_report)
        rollback = load_json(args.rollback_report)
        preflight_errors: list[str] = []
        preflight_errors.extend(
            validate_provider_report(
                provider,
                env=env,
                current_release=current,
                max_age_minutes=settings["provider"],
            )
        )
        preflight_errors.extend(
            validate_live_gate_report(live, max_age_minutes=settings["live"])
        )
        preflight_errors.extend(
            validate_rollback_drill_report(
                rollback,
                env=env,
                max_age_days=settings["rollback"],
            )
        )
        if preflight_errors:
            raise ValueError("Pilot evidence is not admissible: " + "; ".join(dict.fromkeys(preflight_errors)))

        manifest = build_manifest(
            env=env,
            current_release=current,
            previous_release=previous,
            provider_report_path=args.provider_report,
            live_report_path=args.live_report,
            rollback_report_path=args.rollback_report,
            approvals=approvals,
            acknowledgements=acknowledgements,
            max_age_minutes=settings["admission"],
        )
        errors = validate_admission_manifest(
            manifest,
            env=env,
            current_release=current,
            previous_release=previous,
            admission_max_age_minutes=settings["admission"],
            provider_max_age_minutes=settings["provider"],
            live_max_age_minutes=settings["live"],
            rollback_max_age_days=settings["rollback"],
        )
        if errors:
            raise ValueError("Pilot admission manifest is invalid: " + "; ".join(errors))
        atomic_write_json(args.manifest, manifest)
        atomic_write_text(args.manifest.with_suffix(".md"), render_markdown(manifest))
        print(json.dumps({"go": True, "manifest": str(args.manifest), "errors": []}, ensure_ascii=False))
        return 0
    except (OSError, ValueError) as exc:
        print(json.dumps({"go": False, "errors": [str(exc)]}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
