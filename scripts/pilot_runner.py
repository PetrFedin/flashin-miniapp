#!/usr/bin/env python3
"""Admission-gated wrapper for every controlled 20-order pilot operation."""

from __future__ import annotations

import sys
from pathlib import Path

from pilot_admission import verify_default_admission
from pilot_control import main as pilot_control_main
from pilot_launch_admission import verify_final_admission

ROOT = Path(__file__).resolve().parents[1]


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else list(argv)
    errors = verify_default_admission(ROOT)
    if not errors:
        errors = verify_final_admission(root=ROOT)
    if errors:
        print("Pilot runner blocked by admission policy:")
        for error in errors:
            print(f"- {error}")
        return 2
    return pilot_control_main(args)


if __name__ == "__main__":
    raise SystemExit(main())
