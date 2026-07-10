import asyncio
from backend.database import SessionLocal
from backend.services.moysklad import sync_assortment_to_catalog
from backend.services.crm import recompute_all_profiles
from backend.services.recommendations import rebuild_basic_recommendations

async def main():
    db = SessionLocal()
    try:
        log = await sync_assortment_to_catalog(db, sync_type="scheduled")
        crm = recompute_all_profiles(db)
        recs = rebuild_basic_recommendations(db)
        print({"moysklad_sync": log.status, "products_seen": log.products_seen, "crm_profiles": crm, "recommendations": recs})
    finally:
        db.close()

if __name__ == "__main__":
    asyncio.run(main())
