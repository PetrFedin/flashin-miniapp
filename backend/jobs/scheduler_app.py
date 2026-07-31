import asyncio
from datetime import timedelta

from apscheduler.schedulers.blocking import BlockingScheduler

from backend.config import get_settings
from backend.database import SessionLocal, utcnow_naive
from backend.jobs.campaign_jobs import queue_due_campaigns
from backend.jobs.event_jobs import run_event_dispatcher
from backend.jobs.ops_jobs import create_inventory_snapshot, queue_abandoned_cart_notifications
from backend.jobs.outbox_jobs import process_outbox
from backend.jobs.refund_jobs import reconcile_pending_refunds
from backend.jobs.sla_jobs import mark_overdue_sla
from backend.services.crm import recompute_all_profiles
from backend.services.moysklad import sync_assortment_to_catalog
from backend.services.recommendations import rebuild_basic_recommendations


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


async def sync_moysklad_and_rebuild(db):
    sync_log = await sync_assortment_to_catalog(db, sync_type="scheduled")
    if sync_log.status != "success":
        raise RuntimeError(sync_log.error or "MoySklad synchronization failed")

    profiles = recompute_all_profiles(db)
    recommendations = rebuild_basic_recommendations(db)
    return {
        "status": sync_log.status,
        "products_seen": sync_log.products_seen,
        "products_upserted": sync_log.products_upserted,
        "variants_upserted": sync_log.variants_upserted,
        "crm_profiles": profiles,
        "recommendations": recommendations,
    }


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
    scheduler.add_job(
        lambda: with_async_db(sync_moysklad_and_rebuild),
        "interval",
        minutes=moysklad_interval,
        id="moysklad-sync",
        next_run_time=utcnow_naive() + timedelta(seconds=30),
    )
    scheduler.start()


if __name__ == "__main__":
    main()
