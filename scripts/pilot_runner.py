#!/usr/bin/env python3
"""Admission-gated entry point for the executable 20-order pilot control plane."""

from __future__ import annotations

import json
from pathlib import Path

from pilot_admission import verify_default_admission
from pilot_control import DEFAULT_STATE_PATH, main


if __name__ == "__main__":
    if not Path(DEFAULT_STATE_PATH).exists():
        errors = verify_default_admission()
        if errors:
            print(
                json.dumps(
                    {
                        "decision": "NO-GO",
                        "errors": errors,
                        "next": "Create and verify the signed pilot admission manifest before initializing the 20-order pilot.",
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            raise SystemExit(1)
    raise SystemExit(main())
