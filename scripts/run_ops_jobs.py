#!/usr/bin/env python3
from backend.jobs.ops_jobs import create_inventory_snapshot, queue_abandoned_cart_notifications
from backend.jobs.scheduler_lock import run_locked_db_job


if __name__ == "__main__":
    abandoned = run_locked_db_job(
        "abandoned-carts",
        queue_abandoned_cart_notifications,
    )
    inventory = run_locked_db_job(
        "inventory-snapshot",
        create_inventory_snapshot,
    )
    print({"abandoned_carts": abandoned, "inventory_snapshot": inventory})
