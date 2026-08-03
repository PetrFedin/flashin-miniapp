#!/usr/bin/env python3
from backend.jobs.scheduler_lock import run_locked_db_job
from backend.jobs.sla_jobs import mark_overdue_sla


if __name__ == "__main__":
    print(run_locked_db_job("sla", mark_overdue_sla))
