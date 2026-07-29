import asyncio
from types import SimpleNamespace

import pytest

from backend.jobs import scheduler_app


class DummyDb:
    pass


def test_successful_sync_rebuilds_profiles_and_recommendations(monkeypatch):
    async def successful_sync(db, sync_type):
        assert isinstance(db, DummyDb)
        assert sync_type == "scheduled"
        return SimpleNamespace(
            status="success",
            error="",
            products_seen=12,
            products_upserted=3,
            variants_upserted=8,
        )

    calls = {"crm": 0, "recommendations": 0}

    def rebuild_crm(db):
        calls["crm"] += 1
        return 7

    def rebuild_recommendations(db):
        calls["recommendations"] += 1
        return 11

    monkeypatch.setattr(scheduler_app, "sync_assortment_to_catalog", successful_sync)
    monkeypatch.setattr(scheduler_app, "recompute_all_profiles", rebuild_crm)
    monkeypatch.setattr(scheduler_app, "rebuild_basic_recommendations", rebuild_recommendations)

    result = asyncio.run(scheduler_app.sync_moysklad_and_rebuild(DummyDb()))

    assert result == {
        "status": "success",
        "products_seen": 12,
        "products_upserted": 3,
        "variants_upserted": 8,
        "crm_profiles": 7,
        "recommendations": 11,
    }
    assert calls == {"crm": 1, "recommendations": 1}


def test_failed_sync_does_not_rebuild_derived_data(monkeypatch):
    async def failed_sync(db, sync_type):
        return SimpleNamespace(
            status="failed",
            error="provider unavailable",
            products_seen=0,
            products_upserted=0,
            variants_upserted=0,
        )

    def must_not_run(db):
        raise AssertionError("Derived data must not be rebuilt after a failed sync")

    monkeypatch.setattr(scheduler_app, "sync_assortment_to_catalog", failed_sync)
    monkeypatch.setattr(scheduler_app, "recompute_all_profiles", must_not_run)
    monkeypatch.setattr(scheduler_app, "rebuild_basic_recommendations", must_not_run)

    with pytest.raises(RuntimeError, match="provider unavailable"):
        asyncio.run(scheduler_app.sync_moysklad_and_rebuild(DummyDb()))
