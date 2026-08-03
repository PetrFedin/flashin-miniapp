#!/usr/bin/env python3
from backend.jobs.campaign_jobs import queue_due_campaigns
from backend.jobs.scheduler_lock import run_locked_db_job


if __name__ == "__main__":
    print(run_locked_db_job("campaigns", queue_due_campaigns))
