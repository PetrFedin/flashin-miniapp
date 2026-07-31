#!/usr/bin/env python3
from backend.jobs.event_jobs import run_event_dispatcher
from backend.jobs.execution import run_sync_job


if __name__ == "__main__":
    outcome = run_sync_job(
        "events",
        run_event_dispatcher,
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
