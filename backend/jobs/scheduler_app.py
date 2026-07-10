from apscheduler.schedulers.blocking import BlockingScheduler
from backend.database import SessionLocal
from backend.jobs.campaign_jobs import queue_due_campaigns
from backend.jobs.event_jobs import run_event_dispatcher
from backend.jobs.ops_jobs import create_inventory_snapshot, queue_abandoned_cart_notifications
from backend.jobs.outbox_jobs import process_outbox
from backend.jobs.sla_jobs import mark_overdue_sla
import asyncio


def with_db(fn):
    db = SessionLocal()
    try:
        result = fn(db)
        print(fn.__name__, result)
    finally:
        db.close()


def outbox_wrapper():
    db = SessionLocal()
    try:
        print("process_outbox", asyncio.run(process_outbox(db)))
    finally:
        db.close()


def main():
    scheduler = BlockingScheduler()
    scheduler.add_job(lambda: with_db(queue_due_campaigns), "interval", minutes=5)
    scheduler.add_job(lambda: with_db(run_event_dispatcher), "interval", minutes=2)
    scheduler.add_job(lambda: with_db(queue_abandoned_cart_notifications), "interval", minutes=30)
    scheduler.add_job(lambda: with_db(create_inventory_snapshot), "interval", hours=6)
    scheduler.add_job(lambda: with_db(mark_overdue_sla), "interval", minutes=5)
    scheduler.add_job(outbox_wrapper, "interval", minutes=5)
    scheduler.start()


if __name__ == "__main__":
    main()
