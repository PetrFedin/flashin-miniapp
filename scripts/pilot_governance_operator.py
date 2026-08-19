#!/usr/bin/env python3
"""Operator-only entrypoint for GitHub governance evidence creation and verification."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Sequence

import pilot_repository_governance
from pilot_governance_policy import require_trusted_configuration
from pilot_operator_security import require_privileged_token_file_isolation

ROOT = Path(__file__).resolve().parents[1]


def main(argv: Sequence[str] | None = None) -> int:
    # This is the only supported process that may receive PILOT_GITHUB_TOKEN.
    # The application .env must remain token-free even while this command runs.
    require_privileged_token_file_isolation(ROOT)
    env = pilot_repository_governance._runtime_env(ROOT)
    try:
        require_trusted_configuration(env)
    except ValueError as exc:
        print(json.dumps({"go": False, "errors": [str(exc)]}, ensure_ascii=False))
        return 1
    return pilot_repository_governance.main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
