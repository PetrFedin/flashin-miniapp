import asyncio

from apscheduler.schedulers.blocking import BlockingScheduler

from backend.database import SessionLocal
from backend.jobs.campaign_jobs import queue_due_campaigns
from backend.jobs.event_jobs import run_event_dispatcher
from backend.jobs.ops_jobs import create_inventory_snapshot, queue_abandoned_cart_notifications
from backend.jobs.outbox_jobs import process_outbox
from backend.jobs.refund_jobs import reconcile_pending_refunds
from backend.jobs.sla_jobs import mark_overdue_sla


def with_db(fn):
    db = SessionLocal()
    try:
        result = fn(db)
        print(fn.__name__, result)
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def with_async_db(fn):
    db = SessionLocal()
    try:
        result = asyncio.run(fn(db))
        print(fn.__name__, result)
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def main():
    scheduler = BlockingScheduler(job_defaults={"coalesce": True, "max_instances": 1})
    scheduler.add_job(lambda: with_db(queue_due_campaigns), "interval", minutes=5, id="campaigns")
    scheduler.add_job(lambda: with_db(run_event_dispatcher), "interval", minutes=2, id="events")
    scheduler.add_job(
        lambda: with_db(queue_abandoned_cart_notifications),
        "interval",
        minutes=30,
        id="abandoned-carts",
    )
    scheduler.add_job(
        lambda: with_db(create_inventory_snapshot),
        "interval",
        hours=6,
        id="inventory-snapshot",
    )
    scheduler.add_job(lambda: with_db(mark_overdue_sla), "interval", minutes=5, id="sla")
    scheduler.add_job(lambda: with_async_db(process_outbox), "interval", minutes=5, id="outbox")
    scheduler.add_job(
        lambda: with_async_db(reconcile_pending_refunds),
        "interval",
        minutes=5,
        id="refund-reconciliation",
    )
    scheduler.start()


if __name__ == "__main__":
    main()
