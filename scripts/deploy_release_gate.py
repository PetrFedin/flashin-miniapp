#!/usr/bin/env python3
"""Fail closed unless production deploy is bound to one retained immutable release."""

from __future__ import annotations

import argparse
import hashlib
import json
import stat
import subprocess
import zipfile
from pathlib import Path
from typing import Any, Sequence

from release_control import MANIFEST_NAME, tracked_files, verify_release

ROOT = Path(__file__).resolve().parents[1]


def _git(root: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()
        raise ValueError(f"git {' '.join(args)} failed: {detail}")
    return completed.stdout


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _inside(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _load_manifest(archive: Path) -> dict[str, Any]:
    try:
        with zipfile.ZipFile(archive, "r") as bundle:
            payload = json.loads(bundle.read(MANIFEST_NAME))
    except (OSError, zipfile.BadZipFile, KeyError, json.JSONDecodeError) as exc:
        raise ValueError(f"release manifest cannot be loaded: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("release manifest must be a JSON object")
    return payload


def verify_deploy_release(
    root: Path,
    archive: Path,
    *,
    build_dir: Path | None = None,
) -> dict[str, Any]:
    root = root.resolve()
    archive = archive.resolve()
    retained_dir = (build_dir or (root / "deploy/release/builds")).resolve()
    errors: list[str] = []

    if not archive.is_file():
        return {"ok": False, "errors": ["deployment release archive is missing"]}
    if not _inside(archive, retained_dir):
        errors.append("deployment release archive must be retained under deploy/release/builds")

    checksum = archive.with_suffix(archive.suffix + ".sha256")
    if not checksum.is_file():
        errors.append("deployment release checksum sidecar is missing")

    verification = verify_release(archive, checksum=checksum)
    if not verification.get("ok"):
        errors.extend(str(item) for item in verification.get("errors", []))

    try:
        manifest = _load_manifest(archive)
    except ValueError as exc:
        errors.append(str(exc))
        manifest = {}

    try:
        head = _git(root, "rev-parse", "HEAD").strip()
        status = _git(root, "status", "--porcelain=v1", "--untracked-files=all")
    except ValueError as exc:
        errors.append(str(exc))
        head = ""
        status = ""

    if status.strip():
        errors.append(
            "production checkout is not clean; tracked or non-ignored untracked files are present"
        )

    manifest_commit = str(manifest.get("git_commit", "")).strip()
    if len(manifest_commit) != 40:
        errors.append("release manifest git_commit is invalid")
    elif head and manifest_commit != head:
        errors.append("release manifest git_commit does not match checkout HEAD")

    manifest_files = manifest.get("files")
    if not isinstance(manifest_files, dict):
        errors.append("release manifest files map is invalid")
        manifest_files = {}

    try:
        checkout_files = tracked_files(root)
    except RuntimeError as exc:
        errors.append(str(exc))
        checkout_files = []

    if set(checkout_files) != set(manifest_files):
        errors.append("release manifest file set does not match deploy checkout")

    for relative in checkout_files:
        expected = manifest_files.get(relative)
        if not isinstance(expected, dict):
            continue
        source = root / relative
        if not source.is_file() or source.is_symlink():
            errors.append(f"deploy checkout release file is not a regular file: {relative}")
            continue
        if expected.get("sha256") != _sha256(source):
            errors.append(f"deploy checkout file differs from release artifact: {relative}")
        expected_mode = expected.get("mode")
        if isinstance(expected_mode, int):
            actual_mode = stat.S_IMODE(source.stat().st_mode)
            if (actual_mode & 0o111) != (expected_mode & 0o111):
                errors.append(
                    f"deploy checkout executable mode differs from release artifact: {relative}"
                )

    return {
        "ok": not errors,
        "archive": str(archive),
        "checksum": str(checksum),
        "sha256": verification.get("sha256") if verification.get("ok") else None,
        "git_commit": manifest_commit or None,
        "file_count": len(manifest_files),
        "errors": list(dict.fromkeys(errors)),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--print-path", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        report = verify_deploy_release(args.root, args.archive)
    except (OSError, ValueError, RuntimeError) as exc:
        report = {"ok": False, "errors": [str(exc)]}
    if report.get("ok") and args.print_path:
        print(report["archive"])
    else:
        print(json.dumps(report, ensure_ascii=False))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
