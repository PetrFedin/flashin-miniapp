#!/usr/bin/env python3
"""Fail closed unless production deploy is bound to one retained immutable release."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import subprocess
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path
from typing import Any, Mapping, Sequence

from release_ci_gate import select_exact_successful_ci_run, validate_release_context
from release_control import MANIFEST_NAME, tracked_files, verify_release

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_GITHUB_REPOSITORY = "PetrFedin/flashin-miniapp"
DEFAULT_GITHUB_API_URL = "https://api.github.com"
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


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


def _github_get(url: str, token: str = "") -> dict[str, Any]:
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "flashin-production-deploy-gate/1.0",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            payload = json.load(response)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"GitHub API returned HTTP {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"GitHub API request failed: {exc.__class__.__name__}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("GitHub API returned a non-object response")
    return payload


def validate_repository_provenance(
    *,
    expected_sha: str,
    branch_payload: Mapping[str, Any],
    runs_payload: Mapping[str, Any],
    jobs_payload: Mapping[str, Any] | None,
) -> tuple[list[str], Mapping[str, Any] | None]:
    """Validate deploy provenance using the same protected-main policy as Release CI."""

    return validate_release_context(
        ref_name="main",
        expected_sha=expected_sha,
        branch_payload=branch_payload,
        runs_payload=runs_payload,
        jobs_payload=jobs_payload,
    )


def verify_deploy_repository_provenance(
    expected_sha: str,
    *,
    repository: str = DEFAULT_GITHUB_REPOSITORY,
    api_base: str = DEFAULT_GITHUB_API_URL,
    token: str = "",
) -> dict[str, Any]:
    repository = repository.strip()
    expected_sha = expected_sha.strip().lower()
    api_base = api_base.strip().rstrip("/")
    if repository != DEFAULT_GITHUB_REPOSITORY:
        return {
            "ok": False,
            "errors": [
                f"deployment GitHub repository must be {DEFAULT_GITHUB_REPOSITORY}"
            ],
        }
    if api_base != DEFAULT_GITHUB_API_URL:
        return {
            "ok": False,
            "errors": [
                f"deployment GitHub API URL must be {DEFAULT_GITHUB_API_URL}"
            ],
        }
    if not _SHA_RE.fullmatch(expected_sha):
        return {"ok": False, "errors": ["deployment release commit SHA is invalid"]}

    encoded_sha = urllib.parse.quote(expected_sha, safe="")
    branch = _github_get(f"{api_base}/repos/{repository}/branches/main", token)
    runs = _github_get(
        f"{api_base}/repos/{repository}/actions/runs"
        f"?head_sha={encoded_sha}&event=push&status=completed&per_page=100",
        token,
    )
    exact_run = select_exact_successful_ci_run(runs, expected_sha=expected_sha)
    jobs: Mapping[str, Any] | None = None
    if exact_run is not None:
        jobs = _github_get(
            f"{api_base}/repos/{repository}/actions/runs/{int(exact_run['id'])}/jobs?per_page=100",
            token,
        )

    errors, exact_run = validate_repository_provenance(
        expected_sha=expected_sha,
        branch_payload=branch,
        runs_payload=runs,
        jobs_payload=jobs,
    )
    return {
        "ok": not errors,
        "repository": repository,
        "branch": "main",
        "sha": expected_sha,
        "branch_protected": branch.get("protected") is True,
        "exact_push_ci_run_id": exact_run.get("id") if exact_run else None,
        "errors": errors,
    }


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
        if report.get("ok"):
            token = (
                os.getenv("FLASHIN_GITHUB_TOKEN", "").strip()
                or os.getenv("GITHUB_TOKEN", "").strip()
            )
            provenance = verify_deploy_repository_provenance(
                str(report.get("git_commit") or ""),
                repository=DEFAULT_GITHUB_REPOSITORY,
                api_base=DEFAULT_GITHUB_API_URL,
                token=token,
            )
            report["repository_provenance"] = provenance
            if not provenance.get("ok"):
                report["ok"] = False
                report["errors"] = list(
                    dict.fromkeys(
                        [
                            *report.get("errors", []),
                            *(
                                f"repository provenance: {item}"
                                for item in provenance.get("errors", [])
                            ),
                        ]
                    )
                )
    except (OSError, ValueError, RuntimeError) as exc:
        report = {"ok": False, "errors": [str(exc)]}
    if report.get("ok") and args.print_path:
        print(report["archive"])
    else:
        print(json.dumps(report, ensure_ascii=False))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())