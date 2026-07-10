#!/usr/bin/env python3
from backend.database import SessionLocal
from backend.jobs.event_jobs import run_event_dispatcher

if __name__ == "__main__":
    db = SessionLocal()
    try:
        print({"processed_events": run_event_dispatcher(db)})
    finally:
        db.close()
