#!/usr/bin/env python3
"""Create, verify, extract and promote immutable FLASHIN release archives."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import subprocess
import sys
import tempfile
import zipfile
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

SCHEMA_VERSION = 1
MANIFEST_NAME = "release_manifest.json"
DEFAULT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_DIR = DEFAULT_ROOT / "deploy" / "release" / "builds"
DEFAULT_STATE_DIR = DEFAULT_ROOT / "deploy" / "release" / "runtime"
FIXED_ZIP_TIME = (1980, 1, 1, 0, 0, 0)

FORBIDDEN_COMPONENTS = {
    ".git",
    ".github-cache",
    ".pytest_cache",
    "__pycache__",
    "node_modules",
    "backups",
    "media",
    "exports",
    "logs",
    "postgres-data",
    "meili_data",
    "grafana-data",
}
FORBIDDEN_PREFIXES = (
    "deploy/release/builds/",
    "deploy/release/runtime/",
)
PRIVATE_EXACT_PATHS = {
    "docs/pilot/live_pilot_state.json",
    "docs/pilot/live_pilot_summary.md",
    "docs/pilot/integration_check_report.json",
    "docs/pilot/integration_check_report.md",
    "docs/readiness_gate_report.json",
    "docs/readiness_gate_report.md",
    "docs/pilot_live_gate_report.json",
    "docs/pilot_live_gate_report.md",
}


def utc_timestamp(value: datetime | None = None) -> str:
    current = value or datetime.now(UTC)
    if current.tzinfo is None:
        raise ValueError("Timestamp must be timezone-aware")
    return current.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run_git(root: Path, args: list[str]) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise RuntimeError(f"git {' '.join(args)} failed: {detail}")
    return result.stdout


def is_forbidden_path(relative_path: str) -> bool:
    path = PurePosixPath(relative_path)
    if path.is_absolute() or not path.parts or ".." in path.parts:
        return True
    if any(part in FORBIDDEN_COMPONENTS for part in path.parts):
        return True
    if relative_path in PRIVATE_EXACT_PATHS:
        return True
    if relative_path.startswith(FORBIDDEN_PREFIXES):
        return True
    name = path.name
    if name == ".env":
        return True
    if name.startswith(".env.") and not name.endswith(".example"):
        return True
    return False


def tracked_files(root: Path) -> list[str]:
    raw = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-z"],
        capture_output=True,
        check=False,
    )
    if raw.returncode != 0:
        detail = raw.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"git ls-files failed: {detail}")
    files = []
    for item in raw.stdout.split(b"\0"):
        if not item:
            continue
        relative = item.decode("utf-8")
        if not is_forbidden_path(relative):
            files.append(relative)
    return sorted(files)


def assert_clean_checkout(root: Path) -> None:
    status = run_git(root, ["status", "--porcelain", "--untracked-files=no"]).strip()
    if status:
        raise RuntimeError(
            "Tracked files are modified. Commit or revert them before creating a release archive."
        )


def canonical_manifest_bytes(manifest: dict[str, Any]) -> bytes:
    return (
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def zip_info(name: str, mode: int = 0o644) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, FIXED_ZIP_TIME)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = 3
    info.external_attr = (stat.S_IFREG | mode) << 16
    return info


def create_release(
    root: Path,
    output_dir: Path,
    *,
    release_id: str | None = None,
    created_at: str | None = None,
    allow_dirty: bool = False,
) -> dict[str, Any]:
    root = root.resolve()
    output_dir = output_dir.resolve()
    if not allow_dirty:
        assert_clean_checkout(root)

    git_commit = run_git(root, ["rev-parse", "HEAD"]).strip()
    timestamp = created_at or utc_timestamp()
    compact_time = timestamp.replace("-", "").replace(":", "").replace("T", "_")
    resolved_release_id = release_id or f"{compact_time}_{git_commit[:12]}"
    if not resolved_release_id or any(
        ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-"
        for ch in resolved_release_id
    ):
        raise ValueError("release_id may contain only letters, numbers, dot, underscore and dash")

    files = tracked_files(root)
    if not files:
        raise RuntimeError("No tracked files found for release archive")

    manifest_files: dict[str, dict[str, Any]] = {}
    payloads: dict[str, bytes] = {}
    for relative in files:
        source = root / relative
        if not source.is_file() or source.is_symlink():
            raise RuntimeError(f"Tracked release entry must be a regular file: {relative}")
        data = source.read_bytes()
        mode = stat.S_IMODE(source.stat().st_mode)
        payloads[relative] = data
        manifest_files[relative] = {
            "sha256": sha256_bytes(data),
            "size": len(data),
            "mode": mode,
        }

    manifest = {
        "schema_version": SCHEMA_VERSION,
        "release_id": resolved_release_id,
        "created_at": timestamp,
        "git_commit": git_commit,
        "file_count": len(manifest_files),
        "files": manifest_files,
        "policy": {
            "source": "git-tracked-files-only",
            "secrets_included": False,
            "symlinks_allowed": False,
        },
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    archive = output_dir / f"flashin_{resolved_release_id}.zip"
    checksum_path = archive.with_suffix(archive.suffix + ".sha256")
    with tempfile.NamedTemporaryFile(dir=output_dir, delete=False) as temporary:
        temporary_path = Path(temporary.name)
    try:
        with zipfile.ZipFile(
            temporary_path,
            mode="w",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=9,
        ) as bundle:
            for relative in files:
                mode = manifest_files[relative]["mode"]
                bundle.writestr(zip_info(relative, mode), payloads[relative])
            bundle.writestr(zip_info(MANIFEST_NAME), canonical_manifest_bytes(manifest))
        os.replace(temporary_path, archive)
    finally:
        temporary_path.unlink(missing_ok=True)

    archive_sha = sha256_file(archive)
    checksum_path.write_text(f"{archive_sha}  {archive.name}\n", encoding="utf-8")
    return {
        "archive": str(archive),
        "checksum_file": str(checksum_path),
        "sha256": archive_sha,
        "manifest": manifest,
    }


def safe_member_name(name: str) -> bool:
    if "\\" in name:
        return False
    path = PurePosixPath(name)
    return bool(name) and not path.is_absolute() and ".." not in path.parts and name != "."


def verify_release(archive: Path, checksum: Path | None = None) -> dict[str, Any]:
    archive = archive.resolve()
    errors: list[str] = []
    manifest: dict[str, Any] | None = None
    if not archive.is_file():
        return {"ok": False, "archive": str(archive), "errors": ["Archive not found"]}

    archive_sha = sha256_file(archive)
    checksum_path = checksum or archive.with_suffix(archive.suffix + ".sha256")
    if checksum_path.exists():
        tokens = checksum_path.read_text(encoding="utf-8").strip().split()
        expected = tokens[0] if tokens else ""
        if expected != archive_sha:
            errors.append("Archive checksum does not match checksum file")

    try:
        with zipfile.ZipFile(archive, "r") as bundle:
            if bundle.testzip() is not None:
                errors.append("ZIP CRC validation failed")
            infos = bundle.infolist()
            names = [item.filename for item in infos]
            if len(names) != len(set(names)):
                errors.append("Archive contains duplicate paths")
            for info in infos:
                if not safe_member_name(info.filename):
                    errors.append(f"Unsafe archive path: {info.filename}")
                file_type = (info.external_attr >> 16) & 0o170000
                if file_type == stat.S_IFLNK:
                    errors.append(f"Symlink is not allowed: {info.filename}")
                if info.filename != MANIFEST_NAME and is_forbidden_path(info.filename):
                    errors.append(f"Forbidden release path: {info.filename}")

            if names.count(MANIFEST_NAME) != 1:
                errors.append("Archive must contain exactly one release manifest")
            else:
                try:
                    manifest = json.loads(bundle.read(MANIFEST_NAME))
                except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                    errors.append(f"Release manifest is invalid JSON: {exc}")

            if manifest is not None:
                if manifest.get("schema_version") != SCHEMA_VERSION:
                    errors.append("Unsupported release manifest schema")
                manifest_files = manifest.get("files")
                if not isinstance(manifest_files, dict):
                    errors.append("Manifest files must be an object")
                else:
                    archive_files = set(names) - {MANIFEST_NAME}
                    declared_files = set(manifest_files)
                    if archive_files != declared_files:
                        errors.append("Archive file list does not match manifest")
                    if manifest.get("file_count") != len(manifest_files):
                        errors.append("Manifest file_count is incorrect")
                    for relative, expected in manifest_files.items():
                        if relative not in archive_files:
                            continue
                        if is_forbidden_path(relative):
                            errors.append(f"Manifest declares forbidden path: {relative}")
                            continue
                        if not isinstance(expected, dict):
                            errors.append(f"Manifest entry is invalid: {relative}")
                            continue
                        data = bundle.read(relative)
                        if expected.get("sha256") != sha256_bytes(data):
                            errors.append(f"SHA256 mismatch: {relative}")
                        if expected.get("size") != len(data):
                            errors.append(f"Size mismatch: {relative}")
    except (zipfile.BadZipFile, OSError) as exc:
        errors.append(f"Invalid release archive: {exc}")

    return {
        "ok": not errors,
        "archive": str(archive),
        "sha256": archive_sha,
        "checksum_file": str(checksum_path) if checksum_path.exists() else None,
        "release_id": manifest.get("release_id") if manifest else None,
        "git_commit": manifest.get("git_commit") if manifest else None,
        "file_count": manifest.get("file_count") if manifest else None,
        "errors": errors,
    }


def extract_release(archive: Path, destination: Path, *, force: bool = False) -> dict[str, Any]:
    report = verify_release(archive)
    if not report["ok"]:
        raise RuntimeError("Release verification failed: " + "; ".join(report["errors"]))
    destination = destination.resolve()
    if destination.exists() and any(destination.iterdir()) and not force:
        raise RuntimeError("Extraction destination is not empty")
    destination.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(archive.resolve(), "r") as bundle:
        manifest = json.loads(bundle.read(MANIFEST_NAME))
        for relative, metadata in manifest["files"].items():
            target = destination / Path(*PurePosixPath(relative).parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(bundle.read(relative))
            os.chmod(target, int(metadata.get("mode", 0o644)) & 0o777)
        (destination / MANIFEST_NAME).write_bytes(bundle.read(MANIFEST_NAME))
    return {"destination": str(destination), **report}


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def promote_release(archive: Path, state_dir: Path) -> dict[str, Any]:
    report = verify_release(archive)
    if not report["ok"]:
        raise RuntimeError("Release verification failed: " + "; ".join(report["errors"]))
    state_dir = state_dir.resolve()
    current_path = state_dir / "current_release.json"
    previous_path = state_dir / "previous_release.json"
    if current_path.exists():
        current = json.loads(current_path.read_text(encoding="utf-8"))
        atomic_write_json(previous_path, current)
    state = {
        "archive": str(Path(report["archive"]).resolve()),
        "sha256": report["sha256"],
        "release_id": report["release_id"],
        "git_commit": report["git_commit"],
        "promoted_at": utc_timestamp(),
    }
    atomic_write_json(current_path, state)
    return {
        "current": state,
        "previous": (
            json.loads(previous_path.read_text(encoding="utf-8"))
            if previous_path.exists()
            else None
        ),
    }


def resolve_release(slot: str, state_dir: Path) -> Path:
    if slot not in {"current", "previous"}:
        raise ValueError("slot must be current or previous")
    state_path = state_dir.resolve() / f"{slot}_release.json"
    if not state_path.exists():
        raise RuntimeError(f"No {slot} release has been promoted")
    state = json.loads(state_path.read_text(encoding="utf-8"))
    archive = Path(state.get("archive", ""))
    if not archive.is_file():
        raise RuntimeError(f"Promoted {slot} release archive is missing: {archive}")
    report = verify_release(archive)
    if not report["ok"]:
        raise RuntimeError(f"Promoted {slot} release is invalid: {'; '.join(report['errors'])}")
    return archive.resolve()


def parser() -> argparse.ArgumentParser:
    command_parser = argparse.ArgumentParser(description=__doc__)
    sub = command_parser.add_subparsers(dest="command", required=True)

    create = sub.add_parser("create", help="Create an immutable release archive")
    create.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    create.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    create.add_argument("--release-id")
    create.add_argument("--created-at")
    create.add_argument("--allow-dirty", action="store_true")
    create.add_argument("--print-path", action="store_true")

    verify = sub.add_parser("verify", help="Verify release integrity and policy")
    verify.add_argument("--archive", type=Path, required=True)
    verify.add_argument("--checksum", type=Path)

    extract = sub.add_parser("extract", help="Safely extract a verified release")
    extract.add_argument("--archive", type=Path, required=True)
    extract.add_argument("--destination", type=Path, required=True)
    extract.add_argument("--force", action="store_true")

    promote = sub.add_parser("promote", help="Mark a deployed release current")
    promote.add_argument("--archive", type=Path, required=True)
    promote.add_argument("--state-dir", type=Path, default=DEFAULT_STATE_DIR)

    resolve = sub.add_parser("resolve", help="Resolve current or previous release path")
    resolve.add_argument("--slot", choices=("current", "previous"), required=True)
    resolve.add_argument("--state-dir", type=Path, default=DEFAULT_STATE_DIR)

    status = sub.add_parser("status", help="Show local release pointers")
    status.add_argument("--state-dir", type=Path, default=DEFAULT_STATE_DIR)
    return command_parser


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "create":
            result = create_release(
                args.root,
                args.output_dir,
                release_id=args.release_id,
                created_at=args.created_at,
                allow_dirty=args.allow_dirty,
            )
            if args.print_path:
                print(result["archive"])
            else:
                print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        if args.command == "verify":
            report = verify_release(args.archive, args.checksum)
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 0 if report["ok"] else 1
        if args.command == "extract":
            report = extract_release(args.archive, args.destination, force=args.force)
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 0
        if args.command == "promote":
            result = promote_release(args.archive, args.state_dir)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        if args.command == "resolve":
            print(resolve_release(args.slot, args.state_dir))
            return 0
        if args.command == "status":
            result: dict[str, Any] = {}
            for slot in ("current", "previous"):
                path = args.state_dir.resolve() / f"{slot}_release.json"
                result[slot] = (
                    json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
                )
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
        print(f"release-control: {exc}", file=sys.stderr)
        return 1
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
