#!/usr/bin/env python3
import asyncio
from backend.database import SessionLocal
from backend.jobs.outbox_jobs import process_outbox

async def main():
    db = SessionLocal()
    try:
        sent = await process_outbox(db)
        print({"sent_outbox_events": sent})
    finally:
        db.close()

if __name__ == "__main__":
    asyncio.run(main())
