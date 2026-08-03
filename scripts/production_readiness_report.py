#!/usr/bin/env python3
"""Generate a truthful production-readiness report using the strict predeploy gate."""

from __future__ import annotations

import json
from pathlib import Path

from pilot_readiness import build_predeploy_checks, build_report, write_report

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    report = build_report("predeploy", build_predeploy_checks(ROOT))
    json_path, markdown_path = write_report(ROOT, report, stem="production_readiness_report")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print({"json": str(json_path.relative_to(ROOT)), "markdown": str(markdown_path.relative_to(ROOT))})
    return 0 if report["go"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
