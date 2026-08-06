#!/usr/bin/env python3
"""Attach and verify signed live lifecycle evidence on a pilot admission."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from pilot_admission import render_markdown as render_admission_markdown
from pilot_admission import verify_default_admission
from pilot_evidence import (
    atomic_write_json,
    atomic_write_text,
    load_json,
    require_signing_secret,
    sha256_file,
    sign_payload,
)
from pilot_live_lifecycle import validate_live_lifecycle_report
from pilot_readiness import read_env

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / "docs/pilot/pilot_admission_manifest.json"
DEFAULT_REPORT = ROOT / "docs/pilot/live_lifecycle_report.json"
ACKNOWLEDGEMENT_KEY = "live_lifecycle_completed"
EVIDENCE_KEY = "live_lifecycle_report"


def _evidence_entry(
    manifest: Mapping[str, Any],
) -> tuple[Path | None, str, list[str]]:
    evidence = manifest.get("evidence")
    if not isinstance(evidence, Mapping):
        return None, "", ["pilot admission evidence map is missing"]
    entry = evidence.get(EVIDENCE_KEY)
    if not isinstance(entry, Mapping):
        return None, "", ["pilot admission live lifecycle evidence is missing"]
    path = Path(str(entry.get("path", "")).strip())
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

    path, _expected_sha256, entry_errors = _evidence_entry(manifest)
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

    approvals = manifest.get("approvals")
    approved_names = {
        str(value).strip()
        for value in approvals.values()
        if isinstance(approvals, Mapping) and str(value).strip()
    } if isinstance(approvals, Mapping) else set()
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

    if manifest_path.is_file():
        evidence = manifest.get("evidence")
        entry = evidence.get(EVIDENCE_KEY) if isinstance(evidence, Mapping) else None
        if isinstance(entry, Mapping):
            bound_manifest = str(entry.get("admission_manifest_sha256", "")).strip()
            if bound_manifest:
                # The manifest changes when this attachment is added, so the optional
                # reverse binding must refer to the unsigned pre-attachment manifest.
                # It is informational only; the authoritative binding is the signed
                # manifest's exact report path and checksum.
                if len(bound_manifest) != 64:
                    errors.append("live lifecycle admission manifest SHA-256 is invalid")
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
    baseline_errors = verify_default_admission(root)
    if baseline_errors:
        raise ValueError(
            "Baseline pilot admission is invalid: " + "; ".join(baseline_errors)
        )
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
    if lifecycle_errors:
        raise ValueError(
            "Live lifecycle evidence is invalid: " + "; ".join(lifecycle_errors)
        )

    approvals = manifest.get("approvals")
    approved_names = {
        str(value).strip()
        for value in approvals.values()
        if isinstance(approvals, Mapping) and str(value).strip()
    } if isinstance(approvals, Mapping) else set()
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
        "path": str(report_path.resolve()),
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
        errors = verify_default_admission(ROOT)
        if not errors:
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
