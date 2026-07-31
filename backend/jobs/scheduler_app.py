import asyncio
from datetime import UTC, datetime, timedelta
from functools import partial

from apscheduler.schedulers.blocking import BlockingScheduler

from backend.config import get_settings
from backend.jobs.campaign_jobs import queue_due_campaigns
from backend.jobs.event_jobs import run_event_dispatcher
from backend.jobs.execution import run_async_job, run_sync_job
from backend.jobs.moysklad_jobs import sync_moysklad_and_rebuild
from backend.jobs.ops_jobs import create_inventory_snapshot, queue_abandoned_cart_notifications
from backend.jobs.outbox_jobs import process_outbox
from backend.jobs.refund_jobs import reconcile_pending_refunds
from backend.jobs.sla_jobs import mark_overdue_sla


def _print_outcome(outcome) -> None:
    print(
        {
            "job": outcome.job_name,
            "status": outcome.status,
            "run_id": outcome.run_id,
            "result": outcome.result,
        }
    )


def _run_sync(job_name, function):
    outcome = run_sync_job(job_name, function, trigger="scheduler")
    _print_outcome(outcome)
    return outcome


def _run_async(job_name, function):
    outcome = asyncio.run(run_async_job(job_name, function, trigger="scheduler"))
    _print_outcome(outcome)
    return outcome


def _add_interval_job(
    scheduler: BlockingScheduler,
    *,
    job_id: str,
    function,
    minutes: int = 0,
    hours: int = 0,
    async_job: bool = False,
    next_run_time: datetime | None = None,
) -> None:
    runner = partial(_run_async if async_job else _run_sync, job_id, function)
    scheduler.add_job(
        runner,
        "interval",
        id=job_id,
        replace_existing=True,
        minutes=minutes,
        hours=hours,
        next_run_time=next_run_time,
    )


def build_scheduler() -> BlockingScheduler:
    settings = get_settings()
    if not settings.scheduler_enabled:
        raise RuntimeError("Scheduler is disabled by SCHEDULER_ENABLED")

    moysklad_interval = max(5, min(settings.moysklad_sync_interval_minutes, 1440))
    scheduler = BlockingScheduler(
        timezone=UTC,
        job_defaults={
            "coalesce": True,
            "max_instances": 1,
            "misfire_grace_time": 300,
        },
    )
    _add_interval_job(
        scheduler,
        job_id="campaigns",
        function=queue_due_campaigns,
        minutes=5,
    )
    _add_interval_job(
        scheduler,
        job_id="events",
        function=run_event_dispatcher,
        minutes=2,
    )
    _add_interval_job(
        scheduler,
        job_id="abandoned-carts",
        function=queue_abandoned_cart_notifications,
        minutes=30,
    )
    _add_interval_job(
        scheduler,
        job_id="inventory-snapshot",
        function=create_inventory_snapshot,
        hours=6,
    )
    _add_interval_job(
        scheduler,
        job_id="sla",
        function=mark_overdue_sla,
        minutes=5,
    )
    _add_interval_job(
        scheduler,
        job_id="outbox",
        function=process_outbox,
        minutes=5,
        async_job=True,
    )
    _add_interval_job(
        scheduler,
        job_id="refund-reconciliation",
        function=reconcile_pending_refunds,
        minutes=5,
        async_job=True,
    )
    _add_interval_job(
        scheduler,
        job_id="moysklad-sync",
        function=sync_moysklad_and_rebuild,
        minutes=moysklad_interval,
        async_job=True,
        next_run_time=datetime.now(UTC) + timedelta(seconds=30),
    )
    return scheduler


def main():
    scheduler = build_scheduler()
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        if scheduler.running:
            scheduler.shutdown(wait=False)


if __name__ == "__main__":
    main()
