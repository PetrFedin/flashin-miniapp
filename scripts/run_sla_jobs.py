#!/usr/bin/env python3
from backend.jobs.execution import run_sync_job
from backend.jobs.sla_jobs import mark_overdue_sla


if __name__ == "__main__":
    outcome = run_sync_job(
        "sla",
        mark_overdue_sla,
        trigger="worker",
    )
    print(
        {
            "job": outcome.job_name,
            "status": outcome.status,
            "run_id": outcome.run_id,
            "result": outcome.result,
        }
    )
