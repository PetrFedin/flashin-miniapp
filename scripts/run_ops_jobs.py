#!/usr/bin/env python3
from backend.database import SessionLocal
from backend.jobs.ops_jobs import create_inventory_snapshot, queue_abandoned_cart_notifications

if __name__ == "__main__":
    db = SessionLocal()
    try:
        queued = queue_abandoned_cart_notifications(db)
        snapshotted = create_inventory_snapshot(db)
        print({"queued_abandoned_cart_notifications": queued, "inventory_snapshots": snapshotted})
    finally:
        db.close()
