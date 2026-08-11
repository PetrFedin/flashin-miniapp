#!/usr/bin/env python3
"""Read-only staged preflight for the controlled FLASHIN pilot launch.

This command never deploys, creates payments, mutates providers, attaches evidence,
or arms pilot checkout. It composes the existing fail-closed validators and reports
one deterministic next action.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any, Mapping, Sequence

from deploy_release_gate import (
    DEFAULT_GITHUB_API_URL,
    DEFAULT_GITHUB_REPOSITORY,
    verify_deploy_release,
    verify_deploy_repository_provenance,
)
from pilot_admission import load_verified_release_state
from pilot_admission_path import verify_admission_path
from pilot_evidence import load_json
from pilot_governance_admission import validate_attached_governance
from pilot_launch_admission import (
    validate_attached_launch_checklist,
    verify_final_admission,
)
from pilot_lifecycle_admission import validate_attached_lifecycle
from pilot_readiness import read_env
from real_e2e_context_status import inspect_context

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / "docs/pilot/pilot_admission_manifest.json"
DEFAULT_RELEASE_POINTER = ROOT / "deploy/release/runtime/current_release.json"
DEFAULT_CONTEXT = ROOT / "docs/pilot/evidence/real_order_e2e_context.json"
SCHEMA_VERSION = 1

_COMPLETE = "complete"
_READY = "ready"
_BLOCKED = "blocked"


def _stage(
    name: str,
    status: str,
    *,
    errors: Sequence[object] = (),
    next_action: str = "",
    details: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "name": name,
        "status": status,
        "errors": [str(item) for item in errors if str(item).strip()],
        "next_action": next_action,
    }
    if details:
        payload["details"] = dict(details)
    return payload


def _sensitive_values(env: Mapping[str, str]) -> tuple[str, ...]:
    markers = ("TOKEN", "SECRET", "PASSWORD", "API_KEY", "ACCESS_KEY")
    values = {
        str(value).strip()
        for key, value in env.items()
        if any(marker in str(key).upper() for marker in markers)
        and len(str(value).strip()) >= 6
    }
    return tuple(sorted(values, key=len, reverse=True))


def _sanitize_text(value: object, secrets: Sequence[str]) -> str:
    text = str(value)
    for secret in secrets:
        if secret:
            text = text.replace(secret, "[redacted]")
    return text


def _sanitize_report(report: dict[str, Any], env: Mapping[str, str]) -> dict[str, Any]:
    secrets = _sensitive_values(env)
    for stage in report.get("stages", []):
        if not isinstance(stage, dict):
            continue
        stage["errors"] = [
            _sanitize_text(item, secrets) for item in stage.get("errors", [])
        ]
        if stage.get("next_action"):
            stage["next_action"] = _sanitize_text(stage["next_action"], secrets)
        details = stage.get("details")
        if isinstance(details, dict):
            for key, value in list(details.items()):
                if isinstance(value, str):
                    details[key] = _sanitize_text(value, secrets)
    if report.get("next_action"):
        report["next_action"] = _sanitize_text(report["next_action"], secrets)
    return report


def _release_archive_path(root: Path, state: Mapping[str, Any]) -> Path:
    raw = str(state.get("archive") or "").strip()
    if not raw:
        return root / "deploy/release/builds/__missing__.zip"
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = root / path
    return path


def _manifest_or_none(path: Path) -> tuple[Mapping[str, Any] | None, list[str]]:
    try:
        payload = load_json(path)
    except (OSError, ValueError) as exc:
        return None, [str(exc)]
    if not isinstance(payload, Mapping):
        return None, ["pilot admission manifest must be a JSON object"]
    return payload, []


def run_preflight(
    *,
    root: Path = ROOT,
    manifest_path: Path | None = None,
    release_pointer: Path | None = None,
    context_path: Path | None = None,
    local_only: bool = False,
) -> dict[str, Any]:
    """Return a fail-closed launch-stage report without mutating pilot state."""

    root = root.resolve()
    manifest_path = (manifest_path or (root / "docs/pilot/pilot_admission_manifest.json")).resolve()
    release_pointer = (
        release_pointer or (root / "deploy/release/runtime/current_release.json")
    ).resolve()
    context_path = (
        context_path or (root / "docs/pilot/evidence/real_order_e2e_context.json")
    ).resolve()
    env = read_env(root / ".env")
    stages: list[dict[str, Any]] = []

    current_release: Mapping[str, Any] | None = None
    release_sha = ""
    try:
        current_release = load_verified_release_state(release_pointer)
        release_sha = str(current_release.get("git_commit") or "").strip().lower()
        stages.append(
            _stage(
                "release_pointer",
                _COMPLETE,
                details={
                    "release_id": str(current_release.get("release_id") or ""),
                    "git_commit": release_sha,
                },
            )
        )
    except (OSError, ValueError) as exc:
        stages.append(
            _stage(
                "release_pointer",
                _BLOCKED,
                errors=[exc],
                next_action="make release-status",
            )
        )

    if current_release is None:
        stages.append(
            _stage(
                "repository_provenance",
                _BLOCKED,
                errors=["current verified release is required before GitHub provenance can be evaluated"],
                next_action="complete protected-main promotion and rerun make pilot-launch-preflight",
            )
        )
        stages.append(
            _stage(
                "release_checkout",
                _BLOCKED,
                errors=["current verified release is unavailable"],
                next_action="make release-status",
            )
        )
    else:
        if local_only:
            stages.append(
                _stage(
                    "repository_provenance",
                    _BLOCKED,
                    errors=[
                        "remote GitHub provenance check was skipped; local-only mode cannot authorize pilot arm"
                    ],
                    next_action="rerun make pilot-launch-preflight without --local-only",
                    details={"git_commit": release_sha},
                )
            )
        else:
            try:
                token = (
                    os.getenv("FLASHIN_GITHUB_TOKEN", "").strip()
                    or os.getenv("GITHUB_TOKEN", "").strip()
                )
                provenance = verify_deploy_repository_provenance(
                    release_sha,
                    repository=DEFAULT_GITHUB_REPOSITORY,
                    api_base=DEFAULT_GITHUB_API_URL,
                    token=token,
                )
                provenance_errors = [
                    str(item) for item in provenance.get("errors", [])
                ]
                stages.append(
                    _stage(
                        "repository_provenance",
                        _COMPLETE if provenance.get("ok") is True else _BLOCKED,
                        errors=provenance_errors,
                        next_action=(
                            ""
                            if provenance.get("ok") is True
                            else "verify main protection and exact-main push CI, then rerun make pilot-launch-preflight"
                        ),
                        details={
                            "repository": DEFAULT_GITHUB_REPOSITORY,
                            "git_commit": release_sha,
                            "branch_protected": provenance.get("branch_protected") is True,
                            "exact_push_ci_run_id": provenance.get("exact_push_ci_run_id"),
                        },
                    )
                )
            except (OSError, ValueError, RuntimeError) as exc:
                stages.append(
                    _stage(
                        "repository_provenance",
                        _BLOCKED,
                        errors=[exc],
                        next_action="restore read access to GitHub provenance and rerun make pilot-launch-preflight",
                        details={"git_commit": release_sha},
                    )
                )

        archive = _release_archive_path(root, current_release)
        try:
            release_report = verify_deploy_release(root, archive)
            release_errors = [str(item) for item in release_report.get("errors", [])]
            stages.append(
                _stage(
                    "release_checkout",
                    _COMPLETE if release_report.get("ok") is True else _BLOCKED,
                    errors=release_errors,
                    next_action=(
                        ""
                        if release_report.get("ok") is True
                        else "deploy the exact verified current release, then rerun make pilot-launch-preflight"
                    ),
                    details={
                        "git_commit": str(release_report.get("git_commit") or release_sha),
                        "archive_verified": release_report.get("ok") is True,
                    },
                )
            )
        except (OSError, ValueError, RuntimeError) as exc:
            stages.append(
                _stage(
                    "release_checkout",
                    _BLOCKED,
                    errors=[exc],
                    next_action="deploy the exact verified current release, then rerun make pilot-launch-preflight",
                )
            )

    baseline_errors = verify_admission_path(manifest_path, root)
    stages.append(
        _stage(
            "baseline_admission",
            _COMPLETE if not baseline_errors else _BLOCKED,
            errors=baseline_errors,
            next_action=(
                ""
                if not baseline_errors
                else "complete readiness/provider/rollback evidence and run make pilot-admit"
            ),
        )
    )

    context = inspect_context(context_path)
    context_state = str(context.get("state") or "invalid")
    context_details = {
        key: value
        for key, value in context.items()
        if key in {"state", "phase", "order_id", "variant_id", "provider", "provider_payment_id"}
        and value is not None
    }
    if context_state == "ready_for_terminal_verification":
        context_status = _COMPLETE
        context_errors: list[str] = []
        context_action = ""
    elif context_state == "missing":
        context_status = _READY
        context_errors = []
        context_action = "run the controlled deployed payment flow with make real-order-e2e"
    elif context_state == "blocked_recovery":
        context_status = _BLOCKED
        context_errors = [
            "an interrupted controlled real-order run must be reconciled before any retry"
        ]
        context_action = "make real-order-e2e-status and follow docs/pilot/real_provider_e2e_recovery.md"
    else:
        context_status = _BLOCKED
        context_errors = [str(item) for item in context.get("errors", [])] or [
            "real-order E2E context is invalid"
        ]
        context_action = "make real-order-e2e-status and reconcile the private context before continuing"
    stages.append(
        _stage(
            "real_order_context",
            context_status,
            errors=context_errors,
            next_action=context_action,
            details=context_details,
        )
    )

    manifest, manifest_errors = _manifest_or_none(manifest_path)
    if manifest is None:
        lifecycle_errors = list(manifest_errors)
        governance_errors = list(manifest_errors)
        checklist_errors = list(manifest_errors)
    else:
        lifecycle_errors = validate_attached_lifecycle(
            manifest_path,
            manifest,
            env=env,
            root=root,
        )
        governance_errors = validate_attached_governance(
            manifest_path,
            manifest,
            env=env,
            root=root,
        )
        checklist_errors = validate_attached_launch_checklist(
            manifest_path,
            manifest,
            env=env,
            root=root,
        )

    stages.append(
        _stage(
            "live_lifecycle_evidence",
            _COMPLETE if not lifecycle_errors else _BLOCKED,
            errors=lifecycle_errors,
            next_action=(
                ""
                if not lifecycle_errors
                else "complete make real-lifecycle-e2e, then create and attach signed lifecycle evidence"
            ),
        )
    )
    stages.append(
        _stage(
            "repository_governance_evidence",
            _COMPLETE if not governance_errors else _BLOCKED,
            errors=governance_errors,
            next_action=(
                ""
                if not governance_errors
                else "create and attach signed repository-governance evidence for the exact release"
            ),
        )
    )
    stages.append(
        _stage(
            "launch_checklist",
            _COMPLETE if not checklist_errors else _BLOCKED,
            errors=checklist_errors,
            next_action=(
                ""
                if not checklist_errors
                else "complete P01-P20, then run make pilot-checklist-create and make pilot-checklist-attach"
            ),
        )
    )

    final_errors = verify_final_admission(manifest_path, root)
    stages.append(
        _stage(
            "final_admission",
            _COMPLETE if not final_errors else _BLOCKED,
            errors=final_errors,
            next_action=(
                "" if not final_errors else "make pilot-admission-status"
            ),
        )
    )

    all_complete = all(stage.get("status") == _COMPLETE for stage in stages)
    first_incomplete = next(
        (stage for stage in stages if stage.get("status") != _COMPLETE),
        None,
    )
    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "kind": "flashin_pilot_launch_preflight",
        "go": all_complete,
        "meaning": "ready_for_pilot_runtime_arm" if all_complete else "not_ready_for_pilot_runtime_arm",
        "phase": first_incomplete.get("name") if first_incomplete else "runtime_arm",
        "stages": stages,
        "next_action": (
            str(first_incomplete.get("next_action") or "resolve the first incomplete launch stage")
            if first_incomplete
            else "make pilot-runtime-arm"
        ),
    }
    return _sanitize_report(report, env)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--release-pointer", type=Path)
    parser.add_argument("--context", type=Path)
    parser.add_argument(
        "--local-only",
        action="store_true",
        help="Skip GitHub reads for diagnostics only; this mode can never return GO.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        report = run_preflight(
            root=args.root,
            manifest_path=args.manifest,
            release_pointer=args.release_pointer,
            context_path=args.context,
            local_only=args.local_only,
        )
    except (OSError, ValueError, RuntimeError) as exc:
        report = {
            "schema_version": SCHEMA_VERSION,
            "kind": "flashin_pilot_launch_preflight",
            "go": False,
            "meaning": "not_ready_for_pilot_runtime_arm",
            "phase": "preflight_internal_error",
            "stages": [],
            "next_action": "resolve preflight validation error",
            "errors": [str(exc)],
        }
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report.get("go") is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
