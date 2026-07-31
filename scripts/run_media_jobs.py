#!/usr/bin/env python3
from backend.jobs.execution import run_sync_job
from backend.jobs.media_jobs import process_media_jobs, queue_missing_media_jobs


def run_media_pipeline(db):
    return {
        "queued": queue_missing_media_jobs(db),
        "processed": process_media_jobs(db),
    }


if __name__ == "__main__":
    outcome = run_sync_job(
        "media-jobs",
        run_media_pipeline,
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
