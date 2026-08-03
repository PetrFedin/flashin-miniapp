from collections.abc import Awaitable, Callable
from typing import Any

from sqlalchemy.orm import Session

from backend.jobs.scheduler_lock import run_locked_async_db_job
from backend.services.crm import recompute_all_profiles
from backend.services.moysklad import sync_assortment_to_catalog
from backend.services.recommendations import rebuild_basic_recommendations


async def run_moysklad_pipeline(
    db: Session,
    *,
    sync_callback: Callable[..., Awaitable[Any]],
    crm_callback: Callable[[Session], int],
    recommendations_callback: Callable[[Session], int],
) -> dict[str, Any]:
    log = await sync_callback(db, sync_type="scheduled")
    if log.status != "success":
        raise RuntimeError(log.error or "MoySklad synchronization failed")

    crm = crm_callback(db)
    recommendations = recommendations_callback(db)
    return {
        "status": log.status,
        "products_seen": log.products_seen,
        "products_upserted": log.products_upserted,
        "variants_upserted": log.variants_upserted,
        "crm_profiles": crm,
        "recommendations": recommendations,
    }


async def sync_moysklad_and_rebuild(db: Session) -> dict[str, Any]:
    return await run_moysklad_pipeline(
        db,
        sync_callback=sync_assortment_to_catalog,
        crm_callback=recompute_all_profiles,
        recommendations_callback=rebuild_basic_recommendations,
    )


def main():
    outcome = run_locked_async_db_job(
        "moysklad-sync",
        sync_moysklad_and_rebuild,
    )
    print(outcome)
    return outcome


if __name__ == "__main__":
    main()
