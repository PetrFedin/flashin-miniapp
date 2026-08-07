#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import time

from backend.jobs.provider_command_jobs import process_provider_commands
from backend.jobs.scheduler_lock import run_locked_async_db_job


def run_once():
    return run_locked_async_db_job("provider-commands", process_provider_commands)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run durable external-provider commands")
    parser.add_argument(
        "--once",
        action="store_true",
        help="Process one locked batch and exit instead of polling continuously",
    )
    args = parser.parse_args()
    if args.once:
        print(run_once(), flush=True)
        return 0

    raw_poll = os.getenv("PROVIDER_COMMAND_POLL_SECONDS", "15").strip()
    try:
        poll_seconds = int(raw_poll)
    except ValueError as exc:
        raise RuntimeError("PROVIDER_COMMAND_POLL_SECONDS must be an integer") from exc
    if not 5 <= poll_seconds <= 300:
        raise RuntimeError("PROVIDER_COMMAND_POLL_SECONDS must be between 5 and 300")

    while True:
        try:
            print(run_once(), flush=True)
        except Exception as exc:
            print(
                {
                    "job": "provider-commands",
                    "status": "error",
                    "error": str(exc)[:1000],
                },
                flush=True,
            )
        time.sleep(poll_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
