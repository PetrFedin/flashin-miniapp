#!/usr/bin/env python3
from backend.jobs.campaign_jobs import queue_due_campaigns
from backend.jobs.execution import run_sync_job


if __name__ == "__main__":
    outcome = run_sync_job(
        "campaigns",
        queue_due_campaigns,
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
