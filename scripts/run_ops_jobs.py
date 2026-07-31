#!/usr/bin/env python3
from backend.jobs.execution import run_sync_job
from backend.jobs.ops_jobs import create_inventory_snapshot, queue_abandoned_cart_notifications


if __name__ == "__main__":
    abandoned = run_sync_job(
        "abandoned-carts",
        queue_abandoned_cart_notifications,
        trigger="worker",
    )
    inventory = run_sync_job(
        "inventory-snapshot",
        create_inventory_snapshot,
        trigger="worker",
    )
    print(
        {
            "abandoned_carts": {
                "status": abandoned.status,
                "run_id": abandoned.run_id,
                "result": abandoned.result,
            },
            "inventory_snapshot": {
                "status": inventory.status,
                "run_id": inventory.run_id,
                "result": inventory.result,
            },
        }
    )
