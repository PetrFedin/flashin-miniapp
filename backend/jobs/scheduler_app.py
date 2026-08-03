from datetime import timedelta

from apscheduler.schedulers.blocking import BlockingScheduler

from backend.config import get_settings
from backend.database import utcnow_naive
from backend.jobs.campaign_jobs import queue_due_campaigns
from backend.jobs.event_jobs import run_event_dispatcher
from backend.jobs.moysklad_jobs import sync_moysklad_and_rebuild
from backend.jobs.ops_jobs import create_inventory_snapshot, queue_abandoned_cart_notifications
from backend.jobs.outbox_jobs import process_outbox
from backend.jobs.refund_jobs import reconcile_pending_refunds
from backend.jobs.scheduler_lock import run_locked_async_db_job, run_locked_db_job
from backend.jobs.sla_jobs import mark_overdue_sla


def _run_db_job(job_name, callback):
    outcome = run_locked_db_job(job_name, callback)
    print(job_name, outcome)
    return outcome


def _run_async_db_job(job_name, callback):
    outcome = run_locked_async_db_job(job_name, callback)
    print(job_name, outcome)
    return outcome


def main():
    settings = get_settings()
    if not settings.scheduler_enabled:
        raise RuntimeError("Scheduler is disabled by SCHEDULER_ENABLED")

    moysklad_interval = max(5, min(settings.moysklad_sync_interval_minutes, 1440))
    scheduler = BlockingScheduler(
        job_defaults={
            "coalesce": True,
            "max_instances": 1,
            "misfire_grace_time": 300,
        }
    )
    scheduler.add_job(
        lambda: _run_db_job("campaigns", queue_due_campaigns),
        "interval",
        minutes=5,
        id="campaigns",
    )
    scheduler.add_job(
        lambda: _run_db_job("events", run_event_dispatcher),
        "interval",
        minutes=2,
        id="events",
    )
    scheduler.add_job(
        lambda: _run_db_job(
            "abandoned-carts",
            queue_abandoned_cart_notifications,
        ),
        "interval",
        minutes=30,
        id="abandoned-carts",
    )
    scheduler.add_job(
        lambda: _run_db_job("inventory-snapshot", create_inventory_snapshot),
        "interval",
        hours=6,
        id="inventory-snapshot",
    )
    scheduler.add_job(
        lambda: _run_db_job("sla", mark_overdue_sla),
        "interval",
        minutes=5,
        id="sla",
    )
    scheduler.add_job(
        lambda: _run_async_db_job("outbox", process_outbox),
        "interval",
        minutes=5,
        id="outbox",
    )
    scheduler.add_job(
        lambda: _run_async_db_job(
            "refund-reconciliation",
            reconcile_pending_refunds,
        ),
        "interval",
        minutes=5,
        id="refund-reconciliation",
    )
    scheduler.add_job(
        lambda: _run_async_db_job(
            "moysklad-sync",
            sync_moysklad_and_rebuild,
        ),
        "interval",
        minutes=moysklad_interval,
        id="moysklad-sync",
        next_run_time=utcnow_naive() + timedelta(seconds=30),
    )
    scheduler.start()


if __name__ == "__main__":
    main()
