from __future__ import annotations

from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from backend.services.moysklad_operations import compose_moysklad_operations_status


def _now():
    return datetime(2026, 8, 12, 18, 0, 0)


def test_supply_chain_status_surfaces_actionable_counts_without_provider_secrets():
    sync = SimpleNamespace(
        id=1,
        sync_type="manual",
        status="failed",
        products_seen=12,
        products_upserted=3,
        variants_upserted=4,
        error="Bearer super-secret-provider-token raw response body",
        created_at=_now(),
        finished_at=_now(),
    )
    match = SimpleNamespace(
        id=2,
        local_variant_id=11,
        moysklad_id="provider-internal-id-secret",
        external_sku=" EXT-001 ",
        confidence=0.82,
        confirmed=False,
        created_at=_now(),
    )
    reconciliation = SimpleNamespace(
        id=3,
        variant_id=11,
        sku="FLASH-001-M",
        local_stock_qty=5,
        external_stock_qty=3,
        local_reserved_qty=1,
        action="report",
        status="open",
        message="provider payload token=never-copy-me",
        created_at=_now(),
    )
    conflict = SimpleNamespace(
        id=4,
        moysklad_id="another-secret-provider-id",
        sku="FLASH-001-M",
        conflict_type="stock_below_reserved",
        message="raw secret should never be echoed",
        status="open",
        created_at=_now(),
    )

    result = compose_moysklad_operations_status(
        [sync],
        [(match, "FLASH-001-M")],
        [reconciliation],
        [conflict],
    )

    assert result["schema_version"] == 1
    assert result["attention_required"] is True
    assert result["summary"] == {
        "last_sync_status": "failed",
        "last_sync_at": "2026-08-12T18:00:00",
        "pending_matches": 1,
        "open_reconciliations": 1,
        "open_conflicts": 1,
    }
    assert result["reconciliations"][0]["delta"] == -2
    assert result["sku_matches"][0]["external_sku"] == "EXT-001"

    rendered = repr(result)
    for forbidden in (
        "super-secret-provider-token",
        "provider-internal-id-secret",
        "never-copy-me",
        "another-secret-provider-id",
        "raw secret should never be echoed",
        "message",
        "moysklad_id",
        "error\":",
    ):
        assert forbidden not in rendered


def test_supply_chain_status_is_calm_when_no_action_is_required():
    sync = SimpleNamespace(
        id=1,
        sync_type="scheduled",
        status="success",
        products_seen=12,
        products_upserted=0,
        variants_upserted=0,
        error="",
        created_at=_now(),
        finished_at=_now(),
    )
    result = compose_moysklad_operations_status([sync], [], [], [])

    assert result["attention_required"] is False
    assert result["summary"]["last_sync_status"] == "success"
    assert result["summary"]["pending_matches"] == 0
    assert result["summary"]["open_reconciliations"] == 0
    assert result["summary"]["open_conflicts"] == 0


def test_unknown_sync_status_fails_attention_closed_without_reflecting_raw_value():
    sync = SimpleNamespace(
        id=1,
        sync_type="<script>secret-sync</script>",
        status="provider-secret-status",
        products_seen=0,
        products_upserted=0,
        variants_upserted=0,
        error="",
        created_at=_now(),
        finished_at=None,
    )
    result = compose_moysklad_operations_status([sync], [], [], [])

    assert result["attention_required"] is True
    assert result["summary"]["last_sync_status"] == "unknown"
    assert result["sync_logs"][0]["sync_type"] == "unknown"
    assert "provider-secret-status" not in repr(result)
    assert "secret-sync" not in repr(result)


def test_operations_endpoint_is_read_only_products_read_and_uncacheable():
    source = (
        Path(__file__).resolve().parents[1] / "api" / "moysklad.py"
    ).read_text(encoding="utf-8")

    assert '@router.get("/operations-status")' in source
    assert 'require_permission(db, admin, "products.read")' in source
    assert 'response.headers["Cache-Control"] = "no-store, max-age=0"' in source
    assert 'response.headers["Pragma"] = "no-cache"' in source
    assert '@router.post("/operations-status")' not in source
