#!/usr/bin/env python3
"""Fail closed when private pilot lifecycle evidence is tracked by Git."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PRIVATE_PREFIX = "docs/pilot/evidence/"


def tracked_private_evidence(root: Path) -> list[str]:
    result = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-z", "--", PRIVATE_PREFIX],
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError("git ls-files failed")
    return sorted(
        item.decode("utf-8", errors="strict")
        for item in result.stdout.split(b"\0")
        if item
    )


def main() -> int:
    try:
        tracked = tracked_private_evidence(ROOT)
    except (OSError, RuntimeError, UnicodeDecodeError) as exc:
        print(f"release-private-evidence-guard: {exc}", file=sys.stderr)
        return 1
    if tracked:
        print(
            "release-private-evidence-guard: private pilot evidence is tracked by Git; remove it from the index before packaging",
            file=sys.stderr,
        )
        return 1
    print("release-private-evidence-guard: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
