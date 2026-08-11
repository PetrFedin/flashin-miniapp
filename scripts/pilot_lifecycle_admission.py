#!/usr/bin/env python3
"""Attach and verify signed live lifecycle evidence on a pilot admission."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from pilot_admission import render_markdown as render_admission_markdown
from pilot_admission_path import verify_admission_path
from pilot_evidence import (
    atomic_write_json,
    atomic_write_text,
    load_json,
    require_signing_secret,
    sha256_file,
    sign_payload,
)
from pilot_lifecycle_release_guard import require_current_lifecycle_release
from pilot_live_lifecycle import validate_live_lifecycle_report
from pilot_readiness import read_env

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / "docs/pilot/pilot_admission_manifest.json"
DEFAULT_REPORT = ROOT / "docs/pilot/live_lifecycle_report.json"
ACKNOWLEDGEMENT_KEY = "live_lifecycle_completed"
EVIDENCE_KEY = "live_lifecycle_report"
ORDER_CONTEXT_EVIDENCE_PATH = "docs/pilot/evidence/real_order_e2e_context.json"
ORDER_CORRELATED_SCENARIOS = frozenset(
    {
        "yookassa_payment_redirect",
        "yookassa_payment_return",
        "yookassa_duplicate_webhook",
        "yookassa_refund",
        "moysklad_customerorder_outbound",
        "moysklad_demand_outbound",
        "moysklad_salesreturn_outbound",
        "notification_delivery",
    }
)


def _portable_report_path(root: Path, report_path: Path) -> str:
    absolute = report_path.absolute()
    try:
        relative = absolute.relative_to(root.absolute())
    except ValueError as exc:
        raise ValueError("Live lifecycle report must be inside the pilot repository root") from exc
    if relative.parts[:2] != ("docs", "pilot"):
        raise ValueError("Live lifecycle report must be stored under docs/pilot")
    return relative.as_posix()


def _resolve_report_path(root: Path, raw: object) -> Path:
    value = str(raw or "").strip()
    if not value:
        return root / "docs/pilot/__missing__"
    path = Path(value)
    if not path.is_absolute():
        return (root / path).absolute()
    if path.exists():
        return path
    return (root / "docs/pilot" / path.name).absolute()


def validate_order_lifecycle_correlation(
    report: Mapping[str, Any],
    *,
    root: Path | None = None,
    expected_api_base: str | None = None,
) -> list[str]:
    """Require order-linked provider evidence to describe one controlled order."""

    scenarios = report.get("scenarios")
    if not isinstance(scenarios, list):
        return []

    subjects: dict[str, str] = {}
    context_digests: dict[str, str] = {}
    errors: list[str] = []
    for scenario in scenarios:
        if not isinstance(scenario, Mapping):
            continue
        name = str(scenario.get("name", "")).strip()
        if name not in ORDER_CORRELATED_SCENARIOS:
            continue
        subject_id = str(scenario.get("subject_id", "")).strip()
        if subject_id:
            subjects[name] = subject_id

        evidence = scenario.get("evidence")
        context_entries = [
            item
            for item in evidence
            if isinstance(item, Mapping)
            and str(item.get("path", "")).strip() == ORDER_CONTEXT_EVIDENCE_PATH
        ] if isinstance(evidence, list) else []
        if len(context_entries) != 1:
            errors.append(
                f"order-linked scenario {name} must reference exactly one shared real-order E2E context artifact"
            )
            continue
        digest = str(context_entries[0].get("sha256", "")).strip()
        if len(digest) != 64:
            errors.append(
                f"order-linked scenario {name} shared context SHA-256 is invalid"
            )
        else:
            context_digests[name] = digest

    unique_subjects = set(subjects.values())
    if len(unique_subjects) > 1:
        errors.append(
            "order-linked live lifecycle scenarios must share one controlled-order subject_id"
        )
    if len(set(context_digests.values())) > 1:
        errors.append(
            "order-linked live lifecycle scenarios must share one real-order E2E context checksum"
        )

    if root is not None and context_digests:
        context_path = root / ORDER_CONTEXT_EVIDENCE_PATH
        try:
            context = load_json(context_path)
        except (OSError, ValueError) as exc:
            errors.append(f"real-order E2E context artifact is invalid: {exc}")
        else:
            if context.get("schema_version") != 1:
                errors.append("real-order E2E context schema is invalid")
            if context.get("kind") != "flashin_real_order_e2e_context":
                errors.append("real-order E2E context kind is invalid")
            if context.get("provider") != "yookassa":
                errors.append("real-order E2E context provider is invalid")

            context_api_base = str(context.get("api_base") or "").strip().rstrip("/")
            if not context_api_base:
                errors.append("real-order E2E context api_base is invalid")
            if expected_api_base is not None:
                normalized_expected_api_base = str(expected_api_base or "").strip().rstrip("/")
                if not normalized_expected_api_base:
                    errors.append("pilot API_PUBLIC_URL is missing for real-order E2E context validation")
                elif context_api_base != normalized_expected_api_base:
                    errors.append("real-order E2E context api_base does not match pilot API_PUBLIC_URL")

            order_id = context.get("order_id")
            if isinstance(order_id, bool):
                order_id = None
            try:
                normalized_order_id = int(order_id)
            except (TypeError, ValueError):
                normalized_order_id = 0
            if normalized_order_id <= 0:
                errors.append("real-order E2E context order_id is invalid")
            else:
                context_subject = str(context.get("subject_id", "")).strip()
                expected_subject = f"order:{normalized_order_id}"
                if context_subject != expected_subject:
                    errors.append("real-order E2E context subject_id does not match order_id")
                if (
                    context_subject
                    and unique_subjects
                    and any(subject != context_subject for subject in unique_subjects)
                ):
                    errors.append(
                        "real-order E2E context subject_id does not match lifecycle scenario subject_id"
                    )
            try:
                baseline_reserved = int(context.get("baseline_reserved_qty"))
            except (TypeError, ValueError):
                baseline_reserved = -1
            if baseline_reserved != 0:
                errors.append("real-order E2E context baseline reservation must be zero")

    return list(dict.fromkeys(errors))


def _evidence_entry(
    manifest: Mapping[str, Any],
    *,
    root: Path,
) -> tuple[Path | None, str, list[str]]:
    evidence = manifest.get("evidence")
    if not isinstance(evidence, Mapping):
        return None, "", ["pilot admission evidence map is missing"]
    entry = evidence.get(EVIDENCE_KEY)
    if not isinstance(entry, Mapping):
        return None, "", ["pilot admission live lifecycle evidence is missing"]
    path = _resolve_report_path(root, entry.get("path"))
    expected_sha256 = str(entry.get("sha256", "")).strip()
    errors: list[str] = []
    if not path.is_file():
        errors.append("pilot admission live lifecycle evidence file is missing")
    if len(expected_sha256) != 64:
        errors.append("pilot admission live lifecycle evidence SHA-256 is invalid")
    elif path.is_file() and sha256_file(path) != expected_sha256:
        errors.append("pilot admission live lifecycle evidence checksum does not match")
    return path, expected_sha256, errors


def validate_attached_lifecycle(
    manifest_path: Path,
    manifest: Mapping[str, Any],
    *,
    env: Mapping[str, str],
    root: Path = ROOT,
    max_age_hours: int | None = None,
    now=None,
) -> list[str]:
    errors: list[str] = []
    acknowledgements = manifest.get("acknowledgements")
    if not isinstance(acknowledgements, Mapping):
        errors.append("pilot admission acknowledgements are missing")
    elif acknowledgements.get(ACKNOWLEDGEMENT_KEY) is not True:
        errors.append("pilot admission live lifecycle acknowledgement is missing")

    path, _expected_sha256, entry_errors = _evidence_entry(manifest, root=root)
    errors.extend(entry_errors)
    if path is None or not path.is_file():
        return list(dict.fromkeys(errors))
    try:
        report = load_json(path)
    except ValueError as exc:
        return list(dict.fromkeys(errors + [str(exc)]))

    release = manifest.get("release")
    if not isinstance(release, Mapping):
        errors.append("pilot admission release binding is missing")
    else:
        errors.extend(
            validate_live_lifecycle_report(
                report,
                root=root,
                env=env,
                expected_release=release,
                max_age_hours=max_age_hours,
                now=now,
            )
        )
    errors.extend(
        validate_order_lifecycle_correlation(
            report,
            root=root,
            expected_api_base=env.get("API_PUBLIC_URL"),
        )
    )

    approvals = manifest.get("approvals")
    approved_names = (
        {str(value).strip() for value in approvals.values() if str(value).strip()}
        if isinstance(approvals, Mapping)
        else set()
    )
    if not approved_names:
        errors.append("pilot admission named approvals are missing")
    scenarios = report.get("scenarios")
    if isinstance(scenarios, list) and approved_names:
        for scenario in scenarios:
            if not isinstance(scenario, Mapping):
                continue
            owner = str(scenario.get("owner", "")).strip()
            name = str(scenario.get("name", "unknown")).strip()
            if owner not in approved_names:
                errors.append(
                    f"live lifecycle scenario {name} owner is not a signed admission owner"
                )
    return list(dict.fromkeys(errors))


def _render_manifest(manifest: Mapping[str, Any]) -> str:
    base = render_admission_markdown(manifest).rstrip()
    evidence = manifest.get("evidence")
    entry = evidence.get(EVIDENCE_KEY) if isinstance(evidence, Mapping) else None
    path = entry.get("path") if isinstance(entry, Mapping) else "missing"
    digest = entry.get("sha256") if isinstance(entry, Mapping) else "missing"
    return (
        base
        + "\n\n## Live lifecycle evidence\n\n"
        + f"- Report: `{path}`\n"
        + f"- SHA-256: `{digest}`\n"
        + "- Raw Telegram initData and provider secrets are forbidden in the report.\n"
    )


def attach_lifecycle_report(
    manifest_path: Path,
    report_path: Path,
    *,
    root: Path = ROOT,
) -> dict[str, Any]:
    baseline_errors = verify_admission_path(manifest_path, root)
    if baseline_errors:
        raise ValueError(
            "Baseline pilot admission is invalid: " + "; ".join(baseline_errors)
        )
    require_current_lifecycle_release(root)
    env = read_env(root / ".env")
    secret = require_signing_secret(env)
    manifest = load_json(manifest_path)
    report = load_json(report_path)
    release = manifest.get("release")
    if not isinstance(release, Mapping):
        raise ValueError("Pilot admission release binding is missing")
    lifecycle_errors = validate_live_lifecycle_report(
        report,
        root=root,
        env=env,
        expected_release=release,
    )
    lifecycle_errors.extend(
        validate_order_lifecycle_correlation(
            report,
            root=root,
            expected_api_base=env.get("API_PUBLIC_URL"),
        )
    )
    if lifecycle_errors:
        raise ValueError(
            "Live lifecycle evidence is invalid: " + "; ".join(lifecycle_errors)
        )

    approvals = manifest.get("approvals")
    approved_names = (
        {str(value).strip() for value in approvals.values() if str(value).strip()}
        if isinstance(approvals, Mapping)
        else set()
    )
    for scenario in report.get("scenarios", []):
        if not isinstance(scenario, Mapping):
            continue
        owner = str(scenario.get("owner", "")).strip()
        if owner not in approved_names:
            raise ValueError(
                f"Lifecycle owner {owner or 'missing'} is not present in signed admission approvals"
            )

    unsigned = dict(manifest)
    unsigned.pop("signature", None)
    evidence = dict(unsigned.get("evidence") or {})
    evidence[EVIDENCE_KEY] = {
        "path": _portable_report_path(root, report_path),
        "sha256": sha256_file(report_path),
    }
    unsigned["evidence"] = evidence
    acknowledgements = dict(unsigned.get("acknowledgements") or {})
    acknowledgements[ACKNOWLEDGEMENT_KEY] = True
    unsigned["acknowledgements"] = acknowledgements
    signed = sign_payload(unsigned, secret)
    attached_errors = validate_attached_lifecycle(
        manifest_path,
        signed,
        env=env,
        root=root,
    )
    if attached_errors:
        raise ValueError(
            "Attached live lifecycle evidence is invalid: "
            + "; ".join(attached_errors)
        )
    atomic_write_json(manifest_path, signed)
    atomic_write_text(manifest_path.with_suffix(".md"), _render_manifest(signed))
    return signed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    attach = sub.add_parser("attach", help="Attach lifecycle evidence to admission")
    attach.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    attach.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    verify = sub.add_parser("verify", help="Verify admission and lifecycle attachment")
    verify.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "attach":
            manifest = attach_lifecycle_report(args.manifest, args.report, root=ROOT)
            print(
                json.dumps(
                    {
                        "go": True,
                        "manifest": str(args.manifest),
                        "lifecycle": manifest["evidence"][EVIDENCE_KEY],
                    },
                    ensure_ascii=False,
                )
            )
            return 0
        errors = verify_admission_path(args.manifest, ROOT)
        if not errors:
            require_current_lifecycle_release(ROOT)
            manifest = load_json(args.manifest)
            errors.extend(
                validate_attached_lifecycle(
                    args.manifest,
                    manifest,
                    env=read_env(ROOT / ".env"),
                    root=ROOT,
                )
            )
        print(json.dumps({"go": not errors, "errors": errors}, ensure_ascii=False))
        return 0 if not errors else 1
    except (OSError, ValueError) as exc:
        print(json.dumps({"go": False, "errors": [str(exc)]}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
