#!/usr/bin/env python3
import asyncio

from backend.jobs.execution import run_async_job
from backend.jobs.outbox_jobs import process_outbox


async def main():
    outcome = await run_async_job(
        "outbox",
        process_outbox,
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


if __name__ == "__main__":
    asyncio.run(main())
