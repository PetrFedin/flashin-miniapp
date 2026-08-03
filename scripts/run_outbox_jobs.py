#!/usr/bin/env python3
from backend.jobs.outbox_jobs import process_outbox
from backend.jobs.scheduler_lock import run_locked_async_db_job


if __name__ == "__main__":
    print(run_locked_async_db_job("outbox", process_outbox))
