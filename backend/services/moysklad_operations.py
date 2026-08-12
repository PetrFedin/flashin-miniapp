from __future__ import annotations

from typing import Iterable

from sqlalchemy.orm import Session

from ..models import (
    MoySkladConflict,
    MoySkladSkuMatch,
    MoySkladSyncLog,
    ProductVariant,
    StockReconciliationLog,
)

_SYNC_STATUSES = {"started", "success", "failed"}
_SYNC_TYPES = {"manual", "scheduled", "startup", "worker"}
_ROW_STATUSES = {"open", "resolved", "ignored", "fixed", "closed"}
_RECONCILIATION_ACTIONS = {"report", "adjust", "sync", "ignore"}
_MATCH_LIMIT = 100
_RECONCILIATION_LIMIT = 100
_CONFLICT_LIMIT = 100
_SYNC_LOG_LIMIT = 20


def _iso(value) -> str | None:
    return value.isoformat() if value else None


def _safe_enum(value: object, allowed: set[str]) -> str:
    text = str(value or "").strip().lower()
    return text if text in allowed else "unknown"


def _safe_label(value: object, *, max_length: int = 120) -> str:
    text = " ".join(str(value or "").split())
    return text[:max_length]


def _sync_row(row: MoySkladSyncLog) -> dict[str, object]:
    return {
        "id": int(row.id),
        "sync_type": _safe_enum(row.sync_type, _SYNC_TYPES),
        "status": _safe_enum(row.status, _SYNC_STATUSES),
        "products_seen": max(0, int(row.products_seen or 0)),
        "products_upserted": max(0, int(row.products_upserted or 0)),
        "variants_upserted": max(0, int(row.variants_upserted or 0)),
        "has_error": bool(row.error),
        "created_at": _iso(row.created_at),
        "finished_at": _iso(row.finished_at),
    }


def _match_row(row: MoySkladSkuMatch, local_sku: str) -> dict[str, object]:
    confidence = float(row.confidence or 0)
    return {
        "id": int(row.id),
        "local_variant_id": int(row.local_variant_id),
        "local_sku": _safe_label(local_sku),
        "external_sku": _safe_label(row.external_sku),
        "confidence": max(0.0, min(confidence, 1.0)),
        "confirmed": bool(row.confirmed),
        "created_at": _iso(row.created_at),
    }


def _reconciliation_row(row: StockReconciliationLog) -> dict[str, object]:
    local = int(row.local_stock_qty or 0)
    external = int(row.external_stock_qty or 0)
    reserved = max(0, int(row.local_reserved_qty or 0))
    return {
        "id": int(row.id),
        "variant_id": int(row.variant_id),
        "sku": _safe_label(row.sku),
        "local_stock_qty": local,
        "external_stock_qty": external,
        "local_reserved_qty": reserved,
        "delta": external - local,
        "status": _safe_enum(row.status, _ROW_STATUSES),
        "action": _safe_enum(row.action, _RECONCILIATION_ACTIONS),
        "created_at": _iso(row.created_at),
    }


def _conflict_row(row: MoySkladConflict) -> dict[str, object]:
    return {
        "id": int(row.id),
        "sku": _safe_label(row.sku),
        "conflict_type": _safe_label(row.conflict_type),
        "status": _safe_enum(row.status, _ROW_STATUSES),
        "created_at": _iso(row.created_at),
    }


def compose_moysklad_operations_status(
    sync_logs: Iterable[MoySkladSyncLog],
    matches: Iterable[tuple[MoySkladSkuMatch, str]],
    reconciliations: Iterable[StockReconciliationLog],
    conflicts: Iterable[MoySkladConflict],
) -> dict[str, object]:
    safe_sync_logs = [_sync_row(row) for row in sync_logs]
    safe_matches = [_match_row(row, local_sku) for row, local_sku in matches]
    safe_reconciliations = [_reconciliation_row(row) for row in reconciliations]
    safe_conflicts = [_conflict_row(row) for row in conflicts]

    pending_matches = [row for row in safe_matches if not row["confirmed"]]
    open_reconciliations = [
        row for row in safe_reconciliations if row["status"] in {"open", "unknown"}
    ]
    open_conflicts = [row for row in safe_conflicts if row["status"] in {"open", "unknown"}]
    latest_sync = safe_sync_logs[0] if safe_sync_logs else None
    last_sync_status = latest_sync["status"] if latest_sync else "never"
    last_sync_unhealthy = latest_sync is None or last_sync_status != "success"
    last_sync_at = (
        latest_sync["finished_at"] or latest_sync["created_at"]
        if latest_sync
        else None
    )

    return {
        "schema_version": 1,
        "attention_required": bool(
            last_sync_unhealthy or pending_matches or open_reconciliations or open_conflicts
        ),
        "summary": {
            "last_sync_status": last_sync_status,
            "last_sync_at": last_sync_at,
            "pending_matches": len(pending_matches),
            "open_reconciliations": len(open_reconciliations),
            "open_conflicts": len(open_conflicts),
        },
        "sync_logs": safe_sync_logs,
        "sku_matches": safe_matches,
        "reconciliations": safe_reconciliations,
        "conflicts": safe_conflicts,
    }


def build_moysklad_operations_status(db: Session) -> dict[str, object]:
    sync_logs = (
        db.query(MoySkladSyncLog)
        .order_by(MoySkladSyncLog.created_at.desc(), MoySkladSyncLog.id.desc())
        .limit(_SYNC_LOG_LIMIT)
        .all()
    )
    match_rows = (
        db.query(MoySkladSkuMatch, ProductVariant.sku)
        .join(ProductVariant, ProductVariant.id == MoySkladSkuMatch.local_variant_id)
        .order_by(MoySkladSkuMatch.confirmed.asc(), MoySkladSkuMatch.created_at.desc())
        .limit(_MATCH_LIMIT)
        .all()
    )
    reconciliations = (
        db.query(StockReconciliationLog)
        .order_by(StockReconciliationLog.created_at.desc(), StockReconciliationLog.id.desc())
        .limit(_RECONCILIATION_LIMIT)
        .all()
    )
    conflicts = (
        db.query(MoySkladConflict)
        .order_by(MoySkladConflict.created_at.desc(), MoySkladConflict.id.desc())
        .limit(_CONFLICT_LIMIT)
        .all()
    )
    return compose_moysklad_operations_status(sync_logs, match_rows, reconciliations, conflicts)
