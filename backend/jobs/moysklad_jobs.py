from collections.abc import Awaitable, Callable
from typing import Any

from backend.jobs.execution import run_async_job
from backend.services.crm import recompute_all_profiles
from backend.services.moysklad import sync_assortment_to_catalog
from backend.services.recommendations import rebuild_basic_recommendations


async def execute_moysklad_workflow(
    db,
    *,
    sync_function: Callable[..., Awaitable[Any]],
    profile_function: Callable[[Any], int],
    recommendation_function: Callable[[Any], int],
):
    """Run the shared sync/rebuild workflow with explicit dependencies."""

    sync_log = await sync_function(db, sync_type="scheduled")
    if sync_log.status != "success":
        raise RuntimeError(sync_log.error or "MoySklad synchronization failed")

    profiles = profile_function(db)
    recommendations = recommendation_function(db)
    return {
        "status": sync_log.status,
        "products_seen": sync_log.products_seen,
        "products_upserted": sync_log.products_upserted,
        "variants_upserted": sync_log.variants_upserted,
        "crm_profiles": profiles,
        "recommendations": recommendations,
    }


async def sync_moysklad_and_rebuild(db):
    return await execute_moysklad_workflow(
        db,
        sync_function=sync_assortment_to_catalog,
        profile_function=recompute_all_profiles,
        recommendation_function=rebuild_basic_recommendations,
    )


async def main():
    outcome = await run_async_job(
        "moysklad-sync",
        sync_moysklad_and_rebuild,
        trigger="worker",
    )
    print(
        {
            "job": outcome.job_name,
            "status": outcome.status,
            "run_id": outcome.run_id,
            "result": outcome.result,
        }
    )


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
