#!/usr/bin/env python3
from backend.database import SessionLocal
from backend.jobs.media_jobs import queue_missing_media_jobs, process_media_jobs

if __name__ == "__main__":
    db = SessionLocal()
    try:
        queued = queue_missing_media_jobs(db)
        processed = process_media_jobs(db)
        print({"queued": queued, "processed": processed})
    finally:
        db.close()
