#!/usr/bin/env python3
from backend.jobs.media_cleanup_jobs import process_media_cleanup_commands
from backend.jobs.scheduler_lock import run_locked_db_job


def main() -> int:
    outcome = run_locked_db_job("media-cleanup", process_media_cleanup_commands)
    print(outcome)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
