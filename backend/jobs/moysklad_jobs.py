from backend.jobs.scheduler_lock import run_locked_async_db_job
from backend.services.crm import recompute_all_profiles
from backend.services.moysklad import sync_assortment_to_catalog
from backend.services.recommendations import rebuild_basic_recommendations


async def sync_moysklad_and_rebuild(db):
    log = await sync_assortment_to_catalog(db, sync_type="scheduled")
    if log.status != "success":
        raise RuntimeError(log.error or "MoySklad synchronization failed")

    crm = recompute_all_profiles(db)
    recommendations = rebuild_basic_recommendations(db)
    return {
        "status": log.status,
        "products_seen": log.products_seen,
        "products_upserted": log.products_upserted,
        "variants_upserted": log.variants_upserted,
        "crm_profiles": crm,
        "recommendations": recommendations,
    }


def main():
    outcome = run_locked_async_db_job(
        "moysklad-sync",
        sync_moysklad_and_rebuild,
    )
    print(outcome)
    return outcome


if __name__ == "__main__":
    main()
