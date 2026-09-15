from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.orm import Session

from ..models import MoySkladConflict, ProductVariant, StockReconciliationLog
from ..reverse_logistics_models import ReturnLogisticsEvent, ReturnLogisticsItem

_STALE_PHYSICAL_RETURN_CONFLICT = "stale_stock_pending_physical_return"
_BLOCKED_RECONCILIATION_ACTION = "blocked_physical_return"
_CATCHUP_RECONCILIATION_ACTION = "physical_return_catchup"


@dataclass(frozen=True)
class MoySkladStockDecision:
    """Decision at the provider -> local stock authority boundary."""

    target_stock: int
    blocked: bool
    pending_event_ids: tuple[int, ...] = ()


def _provider_identity(variant: ProductVariant) -> str:
    external_id = str(variant.moysklad_id or "").strip()
    return external_id or f"local-variant:{int(variant.id)}"


def _latest_resalable_event(
    db: Session,
    variant_id: int,
) -> tuple[int, datetime] | None:
    row = (
        db.query(ReturnLogisticsEvent.id, ReturnLogisticsEvent.created_at)
        .join(
            ReturnLogisticsItem,
            ReturnLogisticsItem.id == ReturnLogisticsEvent.item_id,
        )
        .filter(
            ReturnLogisticsItem.variant_id == int(variant_id),
            ReturnLogisticsEvent.event_type == "inspected",
            ReturnLogisticsEvent.disposition == "resalable",
            ReturnLogisticsEvent.quantity > 0,
        )
        .order_by(
            ReturnLogisticsEvent.created_at.desc(),
            ReturnLogisticsEvent.id.desc(),
        )
        .first()
    )
    if row is None:
        return None
    return int(row[0]), row[1]


def _latest_catchup(
    db: Session,
    variant_id: int,
) -> StockReconciliationLog | None:
    return (
        db.query(StockReconciliationLog)
        .filter(
            StockReconciliationLog.variant_id == int(variant_id),
            StockReconciliationLog.action == _CATCHUP_RECONCILIATION_ACTION,
            StockReconciliationLog.status == "resolved",
        )
        .order_by(
            StockReconciliationLog.created_at.desc(),
            StockReconciliationLog.id.desc(),
        )
        .first()
    )


def _pending_resalable_event_ids(db: Session, variant_id: int) -> tuple[int, ...]:
    """Return latest physical evidence still awaiting an observed provider catch-up.

    A successfully sent SalesReturn is not enough: MoySklad's inbound stock
    endpoint may still expose an older snapshot. The authority boundary is
    released only after a later inbound snapshot is observed at or above the
    then-current verified local stock.
    """

    latest_event = _latest_resalable_event(db, variant_id)
    if latest_event is None:
        return ()
    event_id, event_created_at = latest_event
    catchup = _latest_catchup(db, variant_id)
    if catchup is not None and catchup.created_at >= event_created_at:
        return ()
    return (event_id,)


def _open_or_refresh_conflict(
    db: Session,
    *,
    variant: ProductVariant,
    external_stock: int,
    pending_event_ids: tuple[int, ...],
) -> None:
    provider_id = _provider_identity(variant)
    event_text = ",".join(str(event_id) for event_id in pending_event_ids)
    message = (
        f"Provider stock {int(external_stock)} would lower verified local stock "
        f"{int(variant.stock_qty)} while resalable inspection event(s) {event_text} "
        "have not been observed in an inbound MoySklad stock snapshot; snapshot was not applied"
    )
    existing = (
        db.query(MoySkladConflict)
        .filter(
            MoySkladConflict.moysklad_id == provider_id,
            MoySkladConflict.conflict_type == _STALE_PHYSICAL_RETURN_CONFLICT,
            MoySkladConflict.status == "open",
        )
        .first()
    )
    if existing is not None:
        existing.sku = str(variant.sku or "")[:120]
        existing.message = message
    else:
        db.add(
            MoySkladConflict(
                moysklad_id=provider_id,
                sku=str(variant.sku or "")[:120],
                conflict_type=_STALE_PHYSICAL_RETURN_CONFLICT,
                message=message,
                status="open",
            )
        )

    reconciliation = (
        db.query(StockReconciliationLog)
        .filter(
            StockReconciliationLog.variant_id == int(variant.id),
            StockReconciliationLog.action == _BLOCKED_RECONCILIATION_ACTION,
            StockReconciliationLog.status == "open",
        )
        .order_by(StockReconciliationLog.id.desc())
        .first()
    )
    if reconciliation is None:
        reconciliation = StockReconciliationLog(
            variant_id=int(variant.id),
            sku=str(variant.sku or "")[:120],
            local_stock_qty=int(variant.stock_qty),
            external_stock_qty=int(external_stock),
            local_reserved_qty=int(variant.reserved_qty or 0),
            action=_BLOCKED_RECONCILIATION_ACTION,
            status="open",
            message=message,
        )
        db.add(reconciliation)
    else:
        reconciliation.local_stock_qty = int(variant.stock_qty)
        reconciliation.external_stock_qty = int(external_stock)
        reconciliation.local_reserved_qty = int(variant.reserved_qty or 0)
        reconciliation.message = message


def _resolve_stale_conflicts(db: Session, variant: ProductVariant) -> None:
    provider_id = _provider_identity(variant)
    rows = (
        db.query(MoySkladConflict)
        .filter(
            MoySkladConflict.moysklad_id == provider_id,
            MoySkladConflict.conflict_type == _STALE_PHYSICAL_RETURN_CONFLICT,
            MoySkladConflict.status == "open",
        )
        .all()
    )
    for row in rows:
        row.status = "resolved"


def _record_catchup(
    db: Session,
    *,
    variant: ProductVariant,
    external_stock: int,
    pending_event_ids: tuple[int, ...],
) -> None:
    event_text = ",".join(str(event_id) for event_id in pending_event_ids)
    db.add(
        StockReconciliationLog(
            variant_id=int(variant.id),
            sku=str(variant.sku or "")[:120],
            local_stock_qty=int(variant.stock_qty),
            external_stock_qty=int(external_stock),
            local_reserved_qty=int(variant.reserved_qty or 0),
            action=_CATCHUP_RECONCILIATION_ACTION,
            status="resolved",
            message=(
                f"Inbound MoySklad stock caught up after resalable inspection event(s) {event_text}"
            ),
        )
    )
    open_reconciliations = (
        db.query(StockReconciliationLog)
        .filter(
            StockReconciliationLog.variant_id == int(variant.id),
            StockReconciliationLog.action == _BLOCKED_RECONCILIATION_ACTION,
            StockReconciliationLog.status == "open",
        )
        .all()
    )
    for row in open_reconciliations:
        row.status = "resolved"


def evaluate_moysklad_stock_snapshot(
    db: Session,
    variant: ProductVariant,
    external_stock: int,
) -> MoySkladStockDecision:
    """Protect verified warehouse truth from a stale downward provider snapshot.

    The protection is scoped to a variant and to resalable physical inspection
    evidence. Financial refunds and damaged/quarantine inspection never create
    it. A provider command being sent does not clear it: only an actually
    observed inbound stock snapshot at or above current local stock does.
    """

    normalized_external = int(external_stock)
    if normalized_external < 0:
        raise ValueError("MoySklad stock cannot be negative")
    current_stock = int(variant.stock_qty or 0)
    reserved_qty = int(variant.reserved_qty or 0)
    target_stock = max(normalized_external, reserved_qty)
    pending_event_ids = _pending_resalable_event_ids(db, int(variant.id))

    if not pending_event_ids:
        _resolve_stale_conflicts(db, variant)
        return MoySkladStockDecision(target_stock=target_stock, blocked=False)

    # Catch-up is proved by the provider's raw stock value, never by the local
    # reserved-quantity floor applied to a lower provider number.
    if normalized_external >= current_stock:
        _record_catchup(
            db,
            variant=variant,
            external_stock=normalized_external,
            pending_event_ids=pending_event_ids,
        )
        _resolve_stale_conflicts(db, variant)
        return MoySkladStockDecision(target_stock=target_stock, blocked=False)

    _open_or_refresh_conflict(
        db,
        variant=variant,
        external_stock=normalized_external,
        pending_event_ids=pending_event_ids,
    )
    return MoySkladStockDecision(
        target_stock=current_stock,
        blocked=True,
        pending_event_ids=pending_event_ids,
    )
