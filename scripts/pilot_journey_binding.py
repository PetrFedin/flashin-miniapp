#!/usr/bin/env python3
"""Cross-bind live lifecycle, P01-P20 checklist and final admission to one pilot journey."""

from __future__ import annotations

import argparse
import json
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Mapping, Sequence

from pilot_admission import load_verified_release_state
from pilot_evidence import (
    atomic_write_json,
    atomic_write_text,
    configuration_fingerprint,
    load_json,
    release_binding,
    require_signing_secret,
    sha256_file,
    sign_payload,
    utc_now,
    utc_timestamp,
    validate_evidence_window,
    validate_release_binding,
    verify_payload_signature,
)
from pilot_launch_admission import verify_final_admission
from pilot_launch_checklist import validate_report as validate_checklist_report
from pilot_live_lifecycle import validate_live_lifecycle_report
from pilot_readiness import read_env

ROOT = Path(__file__).resolve().parents[1]
CURRENT_RELEASE_PATH = ROOT / "deploy/release/runtime/current_release.json"
DEFAULT_ANCHOR = ROOT / "docs/pilot/evidence/controlled_journey_anchor.json"
DEFAULT_LIFECYCLE = ROOT / "docs/pilot/live_lifecycle_report.json"
DEFAULT_CHECKLIST = ROOT / "docs/pilot/launch_checklist_report.json"
DEFAULT_ADMISSION = ROOT / "docs/pilot/pilot_admission_manifest.json"
DEFAULT_REPORT = ROOT / "docs/pilot/journey_binding_report.json"
SCHEMA_VERSION = 1
ANCHOR_KIND = "pilot_controlled_journey_anchor"
REPORT_KIND = "pilot_controlled_journey_binding"
LIFECYCLE_EVIDENCE_KEY = "live_lifecycle_report"
CHECKLIST_EVIDENCE_KEY = "launch_checklist_report"
MAX_ANCHOR_BYTES = 64 * 1024


def _positive_int(
    env: Mapping[str, str],
    key: str,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    raw = str(env.get(key, default)).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{key} must be an integer") from exc
    if value < minimum or value > maximum:
        raise ValueError(f"{key} must be between {minimum} and {maximum}")
    return value


def normalize_journey_id(raw: object) -> str:
    value = str(raw or "").strip().lower()
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError, TypeError) as exc:
        raise ValueError("controlled journey_id must be a canonical UUIDv4") from exc
    if parsed.version != 4 or parsed.int == 0 or value != str(parsed):
        raise ValueError("controlled journey_id must be a canonical UUIDv4")
    return str(parsed)


def _portable_path(root: Path, path: Path, *, directory: tuple[str, ...] | None = None) -> str:
    absolute = path.absolute()
    try:
        relative = absolute.relative_to(root.absolute())
    except ValueError as exc:
        raise ValueError("pilot journey evidence must stay inside the repository root") from exc
    if directory is not None and relative.parts[: len(directory)] != directory:
        raise ValueError("controlled journey anchor must stay under docs/pilot/evidence")
    return relative.as_posix()


def _resolve_path(root: Path, raw: object) -> Path:
    value = str(raw or "").strip()
    if not value:
        return root / "__missing__"
    path = Path(value)
    return path.absolute() if path.is_absolute() else (root / path).absolute()


def build_anchor(
    *,
    env: Mapping[str, str],
    current_release: Mapping[str, Any],
    max_age_hours: int,
    now: datetime | None = None,
    journey_id: str | None = None,
) -> dict[str, Any]:
    secret = require_signing_secret(env)
    created = (now or utc_now()).astimezone(UTC)
    canonical_id = normalize_journey_id(journey_id or str(uuid.uuid4()))
    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "kind": ANCHOR_KIND,
        "created_at": utc_timestamp(created),
        "expires_at": utc_timestamp(created + timedelta(hours=max_age_hours)),
        "journey_id": canonical_id,
        "configuration_fingerprint": configuration_fingerprint(env, secret),
        "release": release_binding(current_release),
    }
    return sign_payload(payload, secret)


def validate_anchor(
    anchor: Mapping[str, Any],
    *,
    env: Mapping[str, str],
    current_release: Mapping[str, Any],
    max_age_hours: int,
    now: datetime | None = None,
) -> list[str]:
    errors: list[str] = []
    try:
        secret = require_signing_secret(env)
    except ValueError as exc:
        return [str(exc)]
    if anchor.get("schema_version") != SCHEMA_VERSION:
        errors.append("unsupported controlled journey anchor schema")
    if anchor.get("kind") != ANCHOR_KIND:
        errors.append("controlled journey anchor kind is invalid")
    try:
        normalize_journey_id(anchor.get("journey_id"))
    except ValueError as exc:
        errors.append(str(exc))
    if not verify_payload_signature(anchor, secret):
        errors.append("controlled journey anchor signature is invalid")
    if anchor.get("configuration_fingerprint") != configuration_fingerprint(env, secret):
        errors.append("controlled journey anchor configuration fingerprint does not match")
    release = anchor.get("release")
    if not isinstance(release, Mapping):
        errors.append("controlled journey anchor release binding is missing")
    else:
        errors.extend(
            f"controlled journey anchor release: {item}"
            for item in validate_release_binding(release, current_release)
        )
    errors.extend(
        validate_evidence_window(
            anchor,
            now=(now or utc_now()).astimezone(UTC),
            maximum_age=timedelta(hours=max_age_hours),
        )
    )
    return list(dict.fromkeys(errors))


def _anchor_file_errors(anchor_path: Path, *, root: Path) -> tuple[str, str, list[str]]:
    errors: list[str] = []
    if anchor_path.is_symlink():
        errors.append("controlled journey anchor must not be a symlink")
    if not anchor_path.is_file():
        return "", "", errors + ["controlled journey anchor file is missing"]
    try:
        portable = _portable_path(root, anchor_path, directory=("docs", "pilot", "evidence"))
    except ValueError as exc:
        return "", "", errors + [str(exc)]
    size = anchor_path.stat().st_size
    if size <= 0 or size > MAX_ANCHOR_BYTES:
        errors.append("controlled journey anchor file size is invalid")
    return portable, sha256_file(anchor_path), list(dict.fromkeys(errors))


def _evidence_references(report: Mapping[str, Any]) -> set[tuple[str, str]]:
    references: set[tuple[str, str]] = set()
    for group_key in ("scenarios", "steps"):
        groups = report.get(group_key)
        if not isinstance(groups, list):
            continue
        for group in groups:
            if not isinstance(group, Mapping):
                continue
            evidence = group.get("evidence")
            if not isinstance(evidence, list):
                continue
            for item in evidence:
                if not isinstance(item, Mapping):
                    continue
                references.add(
                    (
                        str(item.get("path", "")).strip(),
                        str(item.get("sha256", "")).strip(),
                    )
                )
    return references


def anchor_membership_errors(
    lifecycle: Mapping[str, Any],
    checklist: Mapping[str, Any],
    *,
    anchor_path: str,
    anchor_sha256: str,
) -> list[str]:
    expected = (anchor_path, anchor_sha256)
    errors: list[str] = []
    if expected not in _evidence_references(lifecycle):
        errors.append("live lifecycle does not reference the controlled journey anchor")
    if expected not in _evidence_references(checklist):
        errors.append("P01-P20 checklist does not reference the controlled journey anchor")
    return errors


def admission_reference_errors(
    manifest: Mapping[str, Any],
    *,
    lifecycle_sha256: str,
    checklist_sha256: str,
) -> list[str]:
    evidence = manifest.get("evidence")
    if not isinstance(evidence, Mapping):
        return ["final pilot admission evidence map is missing"]
    errors: list[str] = []
    expected = {
        LIFECYCLE_EVIDENCE_KEY: lifecycle_sha256,
        CHECKLIST_EVIDENCE_KEY: checklist_sha256,
    }
    for key, digest in expected.items():
        entry = evidence.get(key)
        if not isinstance(entry, Mapping):
            errors.append(f"final pilot admission is missing {key}")
            continue
        if str(entry.get("sha256", "")).strip() != digest:
            errors.append(f"final pilot admission {key} checksum does not match bound evidence")
    return errors


def _artifact_binding(root: Path, path: Path) -> dict[str, str]:
    return {
        "path": _portable_path(root, path),
        "sha256": sha256_file(path),
    }


def _validate_source_artifacts(
    *,
    root: Path,
    env: Mapping[str, str],
    current_release: Mapping[str, Any],
    anchor_path: Path,
    lifecycle_path: Path,
    checklist_path: Path,
    admission_path: Path,
    max_age_hours: int,
    now: datetime | None = None,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any], str, str, list[str]]:
    errors: list[str] = []
    portable_anchor, anchor_sha, anchor_file_errors = _anchor_file_errors(anchor_path, root=root)
    errors.extend(anchor_file_errors)
    try:
        anchor = load_json(anchor_path)
    except ValueError as exc:
        anchor = {}
        errors.append(str(exc))
    try:
        lifecycle = load_json(lifecycle_path)
    except ValueError as exc:
        lifecycle = {}
        errors.append(str(exc))
    try:
        checklist = load_json(checklist_path)
    except ValueError as exc:
        checklist = {}
        errors.append(str(exc))
    try:
        admission = load_json(admission_path)
    except ValueError as exc:
        admission = {}
        errors.append(str(exc))

    if anchor:
        errors.extend(
            validate_anchor(
                anchor,
                env=env,
                current_release=current_release,
                max_age_hours=max_age_hours,
                now=now,
            )
        )
    if lifecycle:
        errors.extend(
            validate_live_lifecycle_report(
                lifecycle,
                root=root,
                env=env,
                expected_release=current_release,
                max_age_hours=max_age_hours,
                now=now,
            )
        )
    if checklist:
        errors.extend(
            validate_checklist_report(
                checklist,
                root=root,
                env=env,
                expected_release=current_release,
                max_age_hours=max_age_hours,
                now=now,
            )
        )
    if admission:
        errors.extend(verify_final_admission(admission_path, root))

    if lifecycle and checklist and portable_anchor and anchor_sha:
        errors.extend(
            anchor_membership_errors(
                lifecycle,
                checklist,
                anchor_path=portable_anchor,
                anchor_sha256=anchor_sha,
            )
        )
    if admission and lifecycle_path.is_file() and checklist_path.is_file():
        errors.extend(
            admission_reference_errors(
                admission,
                lifecycle_sha256=sha256_file(lifecycle_path),
                checklist_sha256=sha256_file(checklist_path),
            )
        )
    return (
        anchor,
        lifecycle,
        checklist,
        admission,
        portable_anchor,
        anchor_sha,
        list(dict.fromkeys(errors)),
    )


def build_binding_report(
    *,
    root: Path,
    env: Mapping[str, str],
    current_release: Mapping[str, Any],
    anchor_path: Path,
    lifecycle_path: Path,
    checklist_path: Path,
    admission_path: Path,
    max_age_hours: int,
    now: datetime | None = None,
) -> dict[str, Any]:
    created = (now or utc_now()).astimezone(UTC)
    (
        anchor,
        _lifecycle,
        _checklist,
        _admission,
        portable_anchor,
        anchor_sha,
        errors,
    ) = _validate_source_artifacts(
        root=root,
        env=env,
        current_release=current_release,
        anchor_path=anchor_path,
        lifecycle_path=lifecycle_path,
        checklist_path=checklist_path,
        admission_path=admission_path,
        max_age_hours=max_age_hours,
        now=created,
    )
    if errors:
        raise ValueError("; ".join(errors))
    secret = require_signing_secret(env)
    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "kind": REPORT_KIND,
        "created_at": utc_timestamp(created),
        "expires_at": utc_timestamp(created + timedelta(hours=max_age_hours)),
        "go": True,
        "journey_id": normalize_journey_id(anchor.get("journey_id")),
        "configuration_fingerprint": configuration_fingerprint(env, secret),
        "release": release_binding(current_release),
        "anchor": {"path": portable_anchor, "sha256": anchor_sha},
        "live_lifecycle": _artifact_binding(root, lifecycle_path),
        "launch_checklist": _artifact_binding(root, checklist_path),
        "final_admission": _artifact_binding(root, admission_path),
    }
    return sign_payload(payload, secret)


def validate_binding_report(
    report: Mapping[str, Any],
    *,
    root: Path,
    env: Mapping[str, str],
    current_release: Mapping[str, Any],
    max_age_hours: int,
    now: datetime | None = None,
) -> list[str]:
    errors: list[str] = []
    current = (now or utc_now()).astimezone(UTC)
    try:
        secret = require_signing_secret(env)
    except ValueError as exc:
        return [str(exc)]
    if report.get("schema_version") != SCHEMA_VERSION:
        errors.append("unsupported controlled journey binding schema")
    if report.get("kind") != REPORT_KIND:
        errors.append("controlled journey binding kind is invalid")
    if report.get("go") is not True:
        errors.append("controlled journey binding decision is not GO")
    try:
        report_journey_id = normalize_journey_id(report.get("journey_id"))
    except ValueError as exc:
        report_journey_id = ""
        errors.append(str(exc))
    if not verify_payload_signature(report, secret):
        errors.append("controlled journey binding signature is invalid")
    if report.get("configuration_fingerprint") != configuration_fingerprint(env, secret):
        errors.append("controlled journey binding configuration fingerprint does not match")
    release = report.get("release")
    if not isinstance(release, Mapping):
        errors.append("controlled journey binding release is missing")
    else:
        errors.extend(
            f"controlled journey binding release: {item}"
            for item in validate_release_binding(release, current_release)
        )
    errors.extend(
        validate_evidence_window(
            report,
            now=current,
            maximum_age=timedelta(hours=max_age_hours),
        )
    )

    bindings = {
        "anchor": report.get("anchor"),
        "live_lifecycle": report.get("live_lifecycle"),
        "launch_checklist": report.get("launch_checklist"),
        "final_admission": report.get("final_admission"),
    }
    resolved: dict[str, Path] = {}
    for name, raw in bindings.items():
        if not isinstance(raw, Mapping):
            errors.append(f"controlled journey binding {name} is missing")
            continue
        path = _resolve_path(root, raw.get("path"))
        expected_sha = str(raw.get("sha256", "")).strip()
        if not path.is_file():
            errors.append(f"controlled journey binding {name} file is missing")
            continue
        if len(expected_sha) != 64 or sha256_file(path) != expected_sha:
            errors.append(f"controlled journey binding {name} checksum does not match")
            continue
        resolved[name] = path

    if len(resolved) == len(bindings):
        (
            anchor,
            _lifecycle,
            _checklist,
            _admission,
            _portable_anchor,
            _anchor_sha,
            source_errors,
        ) = _validate_source_artifacts(
            root=root,
            env=env,
            current_release=current_release,
            anchor_path=resolved["anchor"],
            lifecycle_path=resolved["live_lifecycle"],
            checklist_path=resolved["launch_checklist"],
            admission_path=resolved["final_admission"],
            max_age_hours=max_age_hours,
            now=current,
        )
        errors.extend(source_errors)
        try:
            anchor_journey_id = normalize_journey_id(anchor.get("journey_id"))
        except ValueError as exc:
            anchor_journey_id = ""
            errors.append(str(exc))
        if report_journey_id and anchor_journey_id and report_journey_id != anchor_journey_id:
            errors.append("controlled journey binding journey_id does not match anchor")
    return list(dict.fromkeys(errors))


def verify_journey_binding(
    root: Path = ROOT,
    report_path: Path | None = None,
) -> list[str]:
    env = read_env(root / ".env")
    try:
        current_release = load_verified_release_state(root / "deploy/release/runtime/current_release.json")
        max_age_hours = _positive_int(
            env,
            "PILOT_JOURNEY_BINDING_MAX_AGE_HOURS",
            24,
            1,
            72,
        )
        report = load_json(report_path or root / "docs/pilot/journey_binding_report.json")
    except (OSError, ValueError) as exc:
        return [str(exc)]
    return validate_binding_report(
        report,
        root=root,
        env=env,
        current_release=current_release,
        max_age_hours=max_age_hours,
    )


def render_markdown(report: Mapping[str, Any]) -> str:
    release = report.get("release") if isinstance(report.get("release"), Mapping) else {}
    return "\n".join(
        [
            "# FLASHIN controlled pilot journey binding",
            "",
            f"Decision: **{'GO' if report.get('go') is True else 'NO-GO'}**",
            "",
            f"Journey: `{report.get('journey_id', 'missing')}`",
            f"Created: `{report.get('created_at', 'unknown')}`",
            f"Expires: `{report.get('expires_at', 'unknown')}`",
            f"Release: `{release.get('release_id', 'unknown')}` / `{release.get('git_commit', 'unknown')}`",
            "",
            "This signed report binds the exact live lifecycle, P01-P20 checklist, final admission and one shared sanitized anchor file.",
            "It contains no customer, Telegram, YooKassa, MoySklad or order identifiers.",
            "",
        ]
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="Create a fresh signed controlled-journey anchor")
    init.add_argument("--anchor", type=Path, default=DEFAULT_ANCHOR)
    init.add_argument("--force", action="store_true")

    create = sub.add_parser("create", help="Create signed cross-artifact journey binding")
    create.add_argument("--anchor", type=Path, default=DEFAULT_ANCHOR)
    create.add_argument("--lifecycle", type=Path, default=DEFAULT_LIFECYCLE)
    create.add_argument("--checklist", type=Path, default=DEFAULT_CHECKLIST)
    create.add_argument("--admission", type=Path, default=DEFAULT_ADMISSION)
    create.add_argument("--report", type=Path, default=DEFAULT_REPORT)

    verify = sub.add_parser("verify", help="Verify controlled journey binding")
    verify.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    env = read_env(ROOT / ".env")
    try:
        current_release = load_verified_release_state(CURRENT_RELEASE_PATH)
        max_age_hours = _positive_int(
            env,
            "PILOT_JOURNEY_BINDING_MAX_AGE_HOURS",
            24,
            1,
            72,
        )
        if args.command == "init":
            if args.anchor.exists() and not args.force:
                raise ValueError("controlled journey anchor already exists; use --force for a new journey")
            _portable_path(ROOT, args.anchor, directory=("docs", "pilot", "evidence"))
            anchor = build_anchor(
                env=env,
                current_release=current_release,
                max_age_hours=max_age_hours,
            )
            atomic_write_json(args.anchor, anchor)
            print(
                json.dumps(
                    {"go": True, "anchor": str(args.anchor), "journey_id": anchor["journey_id"]},
                    ensure_ascii=False,
                )
            )
            return 0
        if args.command == "create":
            report = build_binding_report(
                root=ROOT,
                env=env,
                current_release=current_release,
                anchor_path=args.anchor,
                lifecycle_path=args.lifecycle,
                checklist_path=args.checklist,
                admission_path=args.admission,
                max_age_hours=max_age_hours,
            )
            atomic_write_json(args.report, report)
            atomic_write_text(args.report.with_suffix(".md"), render_markdown(report))
            print(
                json.dumps(
                    {"go": True, "report": str(args.report), "journey_id": report["journey_id"]},
                    ensure_ascii=False,
                )
            )
            return 0
        errors = verify_journey_binding(ROOT, args.report)
        print(json.dumps({"go": not errors, "errors": errors}, ensure_ascii=False))
        return 0 if not errors else 1
    except (OSError, ValueError) as exc:
        print(json.dumps({"go": False, "errors": [str(exc)]}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
