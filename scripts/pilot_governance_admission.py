#!/usr/bin/env python3
"""Attach and verify signed repository-governance evidence on a pilot admission."""

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
from pilot_governance_release_guard import require_current_governance_release
from pilot_lifecycle_admission import validate_attached_lifecycle
from pilot_readiness import read_env
from pilot_repository_governance import validate_report

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / "docs/pilot/pilot_admission_manifest.json"
DEFAULT_REPORT = ROOT / "docs/pilot/repository_governance_report.json"
ACKNOWLEDGEMENT_KEY = "repository_governance_verified"
EVIDENCE_KEY = "repository_governance_report"
LIFECYCLE_EVIDENCE_KEY = "live_lifecycle_report"


def _portable_report_path(root: Path, report_path: Path) -> str:
    absolute = report_path.absolute()
    try:
        relative = absolute.relative_to(root.absolute())
    except ValueError as exc:
        raise ValueError("Repository governance report must be inside the pilot repository root") from exc
    if relative.parts[:2] != ("docs", "pilot"):
        raise ValueError("Repository governance report must be stored under docs/pilot")
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
        return None, "", ["pilot admission repository governance evidence is missing"]
    path = _resolve_report_path(root, entry.get("path"))
    expected_sha256 = str(entry.get("sha256", "")).strip()
    errors: list[str] = []
    if not path.is_file():
        errors.append("pilot admission repository governance evidence file is missing")
    if len(expected_sha256) != 64:
        errors.append("pilot admission repository governance evidence SHA-256 is invalid")
    elif path.is_file() and sha256_file(path) != expected_sha256:
        errors.append("pilot admission repository governance evidence checksum does not match")
    return path, expected_sha256, errors


def validate_attached_governance(
    manifest_path: Path,
    manifest: Mapping[str, Any],
    *,
    env: Mapping[str, str],
    root: Path = ROOT,
    max_age_minutes: int | None = None,
    now=None,
) -> list[str]:
    del manifest_path
    errors: list[str] = []
    acknowledgements = manifest.get("acknowledgements")
    if not isinstance(acknowledgements, Mapping):
        errors.append("pilot admission acknowledgements are missing")
    elif acknowledgements.get(ACKNOWLEDGEMENT_KEY) is not True:
        errors.append("pilot admission repository governance acknowledgement is missing")

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
            validate_report(
                report,
                env=env,
                expected_release=release,
                max_age_minutes=max_age_minutes,
                now=now,
            )
        )

    approvals = manifest.get("approvals")
    technical_owner = (
        str(approvals.get("technical_owner", "")).strip()
        if isinstance(approvals, Mapping)
        else ""
    )
    if not technical_owner:
        errors.append("pilot admission technical owner is missing")
    elif str(report.get("owner", "")).strip() != technical_owner:
        errors.append("repository governance owner is not the signed technical owner")
    return list(dict.fromkeys(errors))


def _render_evidence_block(
    title: str,
    entry: object,
    note: str,
) -> str:
    normalized = entry if isinstance(entry, Mapping) else {}
    path = normalized.get("path", "missing")
    digest = normalized.get("sha256", "missing")
    return (
        f"\n\n## {title}\n\n"
        + f"- Report: `{path}`\n"
        + f"- SHA-256: `{digest}`\n"
        + f"- {note}\n"
    )


def _render_manifest(manifest: Mapping[str, Any]) -> str:
    base = render_admission_markdown(manifest).rstrip()
    evidence = manifest.get("evidence")
    evidence_map = evidence if isinstance(evidence, Mapping) else {}
    return (
        base
        + _render_evidence_block(
            "Live lifecycle evidence",
            evidence_map.get(LIFECYCLE_EVIDENCE_KEY),
            "Raw Telegram initData and provider secrets are forbidden in the report.",
        )
        + _render_evidence_block(
            "Repository governance evidence",
            evidence_map.get(EVIDENCE_KEY),
            "The protected branch, exact release commit and successful required CI are immutable pilot inputs.",
        )
    )


def attach_governance_report(
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
    current_release = require_current_governance_release(root)
    env = read_env(root / ".env")
    secret = require_signing_secret(env)
    manifest = load_json(manifest_path)
    lifecycle_errors = validate_attached_lifecycle(
        manifest_path,
        manifest,
        env=env,
        root=root,
    )
    if lifecycle_errors:
        raise ValueError(
            "Live lifecycle attachment is invalid: " + "; ".join(lifecycle_errors)
        )
    report = load_json(report_path)
    release = manifest.get("release")
    if not isinstance(release, Mapping):
        raise ValueError("Pilot admission release binding is missing")
    if any(
        str(release.get(key, "")) != str(current_release.get(key, ""))
        for key in ("release_id", "git_commit", "sha256")
    ):
        raise ValueError("Pilot admission release does not match current governance-capable release")
    governance_errors = validate_report(
        report,
        env=env,
        expected_release=release,
    )
    if governance_errors:
        raise ValueError(
            "Repository governance evidence is invalid: " + "; ".join(governance_errors)
        )

    approvals = manifest.get("approvals")
    technical_owner = (
        str(approvals.get("technical_owner", "")).strip()
        if isinstance(approvals, Mapping)
        else ""
    )
    if str(report.get("owner", "")).strip() != technical_owner:
        raise ValueError("Repository governance owner must equal the signed technical owner")

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
    attached_errors = validate_attached_governance(
        manifest_path,
        signed,
        env=env,
        root=root,
    )
    if attached_errors:
        raise ValueError(
            "Attached repository governance evidence is invalid: "
            + "; ".join(attached_errors)
        )
    atomic_write_json(manifest_path, signed)
    atomic_write_text(manifest_path.with_suffix(".md"), _render_manifest(signed))
    return signed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    attach = sub.add_parser("attach", help="Attach governance evidence to admission")
    attach.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    attach.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    verify = sub.add_parser("verify", help="Verify admission, lifecycle and governance")
    verify.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "attach":
            manifest = attach_governance_report(args.manifest, args.report, root=ROOT)
            print(
                json.dumps(
                    {
                        "go": True,
                        "manifest": str(args.manifest),
                        "governance": manifest["evidence"][EVIDENCE_KEY],
                    },
                    ensure_ascii=False,
                )
            )
            return 0
        errors = verify_default_admission(ROOT)
        if not errors:
            require_current_governance_release(ROOT)
            manifest = load_json(args.manifest)
            env = read_env(ROOT / ".env")
            errors.extend(
                validate_attached_lifecycle(
                    args.manifest,
                    manifest,
                    env=env,
                    root=ROOT,
                )
            )
            errors.extend(
                validate_attached_governance(
                    args.manifest,
                    manifest,
                    env=env,
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
