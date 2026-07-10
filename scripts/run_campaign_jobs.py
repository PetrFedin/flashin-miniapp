#!/usr/bin/env python3
from backend.database import SessionLocal
from backend.jobs.campaign_jobs import queue_due_campaigns

if __name__ == "__main__":
    db = SessionLocal()
    try:
        queued = queue_due_campaigns(db)
        print({"queued_campaigns": queued})
    finally:
        db.close()
