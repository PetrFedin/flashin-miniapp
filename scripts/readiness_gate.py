#!/usr/bin/env python3
"""Strict pre-deploy and live pilot GO/NO-GO gate."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from pilot_readiness import (
    build_live_checks,
    build_predeploy_checks,
    build_report,
    write_report,
)

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description="FLASHIN pilot readiness gate")
    parser.add_argument(
        "--phase",
        choices=("predeploy", "live"),
        default="predeploy",
        help="predeploy validates launch inputs; live also probes public endpoints",
    )
    args = parser.parse_args()

    checks = build_predeploy_checks(ROOT)
    if args.phase == "live":
        checks.extend(build_live_checks(ROOT))

    report = build_report(args.phase, checks)
    stem = "readiness_gate_report" if args.phase == "predeploy" else "pilot_live_gate_report"
    json_path, markdown_path = write_report(ROOT, report, stem=stem)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print({"json": str(json_path.relative_to(ROOT)), "markdown": str(markdown_path.relative_to(ROOT))})
    return 0 if report["go"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
