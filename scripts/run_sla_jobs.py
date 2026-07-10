#!/usr/bin/env python3
from backend.database import SessionLocal
from backend.jobs.sla_jobs import mark_overdue_sla

if __name__ == "__main__":
    db = SessionLocal()
    try:
        overdue = mark_overdue_sla(db)
        print({"overdue_sla": overdue})
    finally:
        db.close()
