from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from ..models import MoySkladConflict, ProductVariant
from ..provider_models import ProviderCommand
from ..reverse_logistics_models import ReturnLogisticsCase, ReturnLogisticsItem

_STALE_PHYSICAL_RETURN_CONFLICT = "stale_stock_pending_physical_return"
_PHYSICAL_RETURN_COMMAND = "moysklad.physical_sales_return.create"


@dataclass(frozen=True)
class MoySkladStockDecision:
    """Decision at the provider -> local stock authority boundary."""

    target_stock: int
    blocked: bool
    pending_case_ids: tuple[int, ...] = ()


def _provider_identity(variant: ProductVariant) -> str:
    external_id = str(variant.moysklad_id or "").strip()
    return external_id or f"local-variant:{int(variant.id)}"


def _pending_physical_return_case_ids(db: Session, variant_id: int) -> tuple[int, ...]:
    """Physical resalable cases not yet acknowledged by a MoySklad SalesReturn.

    A warehouse inspection is authoritative locally before the asynchronous
    provider command completes. While that command is absent, pending,
    processing, failed or review-required, a lower inbound provider snapshot is
    stale with respect to the verified warehouse receipt and must not erase it.
    """

    case_ids = [
        int(row[0])
        for row in (
            db.query(ReturnLogisticsItem.case_id)
            .join(
                ReturnLogisticsCase,
                ReturnLogisticsCase.id == ReturnLogisticsItem.case_id,
            )
            .filter(
                ReturnLogisticsItem.variant_id == int(variant_id),
                ReturnLogisticsItem.resalable_qty > 0,
                ReturnLogisticsCase.status == "inspected",
            )
            .distinct()
            .order_by(ReturnLogisticsItem.case_id.asc())
            .all()
        )
    ]
    if not case_ids:
        return ()

    commands = (
        db.query(ProviderCommand)
        .filter(
            ProviderCommand.provider == "moysklad",
            ProviderCommand.command_type == _PHYSICAL_RETURN_COMMAND,
            ProviderCommand.aggregate_type == "return_logistics_case",
            ProviderCommand.aggregate_id.in_([str(case_id) for case_id in case_ids]),
        )
        .order_by(ProviderCommand.id.asc())
        .all()
    )
    acknowledged: set[int] = set()
    for command in commands:
        if command.status != "sent" or not str(command.external_id or "").strip():
            continue
        try:
            acknowledged.add(int(command.aggregate_id))
        except (TypeError, ValueError):
            continue

    return tuple(case_id for case_id in case_ids if case_id not in acknowledged)


def _open_or_refresh_conflict(
    db: Session,
    *,
    variant: ProductVariant,
    external_stock: int,
    pending_case_ids: tuple[int, ...],
) -> None:
    provider_id = _provider_identity(variant)
    existing = (
        db.query(MoySkladConflict)
        .filter(
            MoySkladConflict.moysklad_id == provider_id,
            MoySkladConflict.conflict_type == _STALE_PHYSICAL_RETURN_CONFLICT,
            MoySkladConflict.status == "open",
        )
        .first()
    )
    case_text = ",".join(str(case_id) for case_id in pending_case_ids)
    message = (
        f"Provider stock {int(external_stock)} would lower verified local stock "
        f"{int(variant.stock_qty)} while physical return case(s) {case_text} "
        "are not acknowledged by MoySklad; snapshot was not applied"
    )
    if existing is not None:
        existing.sku = str(variant.sku or "")[:120]
        existing.message = message
        return
    db.add(
        MoySkladConflict(
            moysklad_id=provider_id,
            sku=str(variant.sku or "")[:120],
            conflict_type=_STALE_PHYSICAL_RETURN_CONFLICT,
            message=message,
            status="open",
        )
    )


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


def evaluate_moysklad_stock_snapshot(
    db: Session,
    variant: ProductVariant,
    external_stock: int,
) -> MoySkladStockDecision:
    """Protect verified warehouse truth from a stale downward provider snapshot.

    Upward/equal snapshots are always safe. A downward snapshot is blocked only
    while at least one inspected resalable physical return for this variant has
    not reached a successful MoySklad physical SalesReturn command. This keeps
    the protection scoped to the asynchronous/review window rather than turning
    historical returns into a permanent stock floor.
    """

    normalized_external = int(external_stock)
    if normalized_external < 0:
        raise ValueError("MoySklad stock cannot be negative")
    current_stock = int(variant.stock_qty or 0)
    reserved_qty = int(variant.reserved_qty or 0)
    target_stock = max(normalized_external, reserved_qty)

    if target_stock >= current_stock:
        _resolve_stale_conflicts(db, variant)
        return MoySkladStockDecision(target_stock=target_stock, blocked=False)

    pending_case_ids = _pending_physical_return_case_ids(db, int(variant.id))
    if not pending_case_ids:
        _resolve_stale_conflicts(db, variant)
        return MoySkladStockDecision(target_stock=target_stock, blocked=False)

    _open_or_refresh_conflict(
        db,
        variant=variant,
        external_stock=normalized_external,
        pending_case_ids=pending_case_ids,
    )
    return MoySkladStockDecision(
        target_stock=current_stock,
        blocked=True,
        pending_case_ids=pending_case_ids,
    )
