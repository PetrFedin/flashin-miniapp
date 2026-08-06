#!/usr/bin/env python3
"""Operator-only entrypoint for GitHub governance evidence creation and verification."""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

import pilot_repository_governance
from pilot_operator_security import require_privileged_token_isolation

ROOT = Path(__file__).resolve().parents[1]


def main(argv: Sequence[str] | None = None) -> int:
    require_privileged_token_isolation(ROOT)
    return pilot_repository_governance.main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
