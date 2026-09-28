from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from ..models import (
    InventoryMovement,
    MoySkladConflict,
    ProductVariant,
    StockReconciliationLog,
)
from ..provider_models import ProviderCommand
from ..reverse_logistics_models import ReturnLogisticsEvent, ReturnLogisticsItem

_STALE_PHYSICAL_RETURN_CONFLICT = "stale_stock_pending_physical_return"
_BLOCKED_RECONCILIATION_ACTION = "blocked_physical_return"
_CATCHUP_RECONCILIATION_ACTION = "physical_return_catchup"
_PHYSICAL_RETURN_COMMAND = "moysklad.physical_sales_return.create"
_QUARANTINE_MOVE_COMMAND = "moysklad.quarantine_move.create"
_OPEN_EVIDENCE_CONSTRAINTS = {
    "uq_moysklad_conflict_open_stale_physical_return",
    "uq_stock_reconciliation_open_blocked_physical_return",
}


@dataclass(frozen=True)
class MoySkladStockDecision:
    """Decision at the provider -> local stock authority boundary."""

    target_stock: int
    blocked: bool
    pending_event_ids: tuple[int, ...] = ()


@dataclass(frozen=True)
class _PendingReturnEvidence:
    event_id: int
    case_id: int
    event_type: str
    quantity: int
    created_at: datetime

    @property
    def source(self) -> str:
        return f"reverse_logistics_event:{self.event_id}"


@dataclass(frozen=True)
class _VariantAuthoritySnapshot:
    """Primitive stock identity safe to pass into an evidence-only transaction."""

    variant_id: int
    provider_id: str
    sku: str
    stock_qty: int
    reserved_qty: int


def _provider_identity(variant: ProductVariant) -> str:
    external_id = str(variant.moysklad_id or "").strip()
    return external_id or f"local-variant:{int(variant.id)}"


def _variant_authority_snapshot(variant: ProductVariant) -> _VariantAuthoritySnapshot:
    return _VariantAuthoritySnapshot(
        variant_id=int(variant.id),
        provider_id=_provider_identity(variant),
        sku=str(variant.sku or "")[:120],
        stock_qty=int(variant.stock_qty or 0),
        reserved_qty=int(variant.reserved_qty or 0),
    )


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


def _pending_resalable_evidence(
    db: Session,
    variant_id: int,
) -> tuple[_PendingReturnEvidence, ...]:
    """Load resalable physical events newer than the last proven provider catch-up."""

    query = (
        db.query(
            ReturnLogisticsEvent.id,
            ReturnLogisticsEvent.case_id,
            ReturnLogisticsEvent.event_type,
            ReturnLogisticsEvent.quantity,
            ReturnLogisticsEvent.created_at,
        )
        .join(
            ReturnLogisticsItem,
            ReturnLogisticsItem.id == ReturnLogisticsEvent.item_id,
        )
        .filter(
            ReturnLogisticsItem.variant_id == int(variant_id),
            ReturnLogisticsItem.case_id == ReturnLogisticsEvent.case_id,
            ReturnLogisticsEvent.event_type.in_(("inspected", "reclassified")),
            ReturnLogisticsEvent.disposition == "resalable",
            ReturnLogisticsEvent.quantity > 0,
        )
    )
    catchup = _latest_catchup(db, variant_id)
    if catchup is not None:
        query = query.filter(ReturnLogisticsEvent.created_at > catchup.created_at)
    rows = query.order_by(
        ReturnLogisticsEvent.created_at.asc(),
        ReturnLogisticsEvent.id.asc(),
    ).all()
    return tuple(
        _PendingReturnEvidence(
            event_id=int(event_id),
            case_id=int(case_id),
            event_type=str(event_type),
            quantity=int(quantity),
            created_at=created_at,
        )
        for event_id, case_id, event_type, quantity, created_at in rows
    )


def _physical_ledger_floor(
    db: Session,
    *,
    variant_id: int,
    evidence: tuple[_PendingReturnEvidence, ...],
) -> int | None:
    """Return immutable local post-return stock floor, or fail closed on bad evidence."""

    if not evidence:
        return None
    by_source = {
        str(row.source): row
        for row in (
            db.query(InventoryMovement)
            .filter(
                InventoryMovement.variant_id == int(variant_id),
                InventoryMovement.kind == "return",
                InventoryMovement.source.in_([item.source for item in evidence]),
            )
            .all()
        )
    }
    stock_after: list[int] = []
    for item in evidence:
        movement = by_source.get(item.source)
        if movement is None or int(movement.quantity) != item.quantity:
            return None
        stock_after.append(int(movement.stock_after))
    return max(stock_after) if stock_after else None


def _provider_returns_exported(
    db: Session,
    evidence: tuple[_PendingReturnEvidence, ...],
) -> bool:
    """Require exact provider evidence for each local sellable-stock increase."""

    if not evidence:
        return False
    for item in evidence:
        if item.event_type == "inspected":
            command_type = _PHYSICAL_RETURN_COMMAND
            aggregate_type = "return_logistics_case"
            aggregate_id = str(item.case_id)
        elif item.event_type == "reclassified":
            command_type = _QUARANTINE_MOVE_COMMAND
            aggregate_type = "return_logistics_event"
            aggregate_id = str(item.event_id)
        else:
            return False
        command = (
            db.query(ProviderCommand)
            .filter(
                ProviderCommand.provider == "moysklad",
                ProviderCommand.command_type == command_type,
                ProviderCommand.aggregate_type == aggregate_type,
                ProviderCommand.aggregate_id == aggregate_id,
            )
            .order_by(ProviderCommand.id.desc())
            .first()
        )
        if (
            command is None
            or command.status != "sent"
            or not str(command.external_id or "").strip()
        ):
            return False
    return True


def _open_or_refresh_conflict(
    db: Session,
    *,
    variant: _VariantAuthoritySnapshot,
    external_stock: int,
    pending_event_ids: tuple[int, ...],
    provider_exported: bool,
    catchup_floor: int | None,
) -> None:
    event_text = ",".join(str(event_id) for event_id in pending_event_ids)
    floor_text = "invalid/missing" if catchup_floor is None else str(catchup_floor)
    message = (
        f"Provider stock {int(external_stock)} is not authoritative for local stock "
        f"{variant.stock_qty} while resalable physical event(s) {event_text} "
        f"await provider reconciliation; provider_exported={provider_exported}; "
        f"required_catchup_stock={floor_text}; snapshot was not applied"
    )
    existing = (
        db.query(MoySkladConflict)
        .filter(
            MoySkladConflict.moysklad_id == variant.provider_id,
            MoySkladConflict.conflict_type == _STALE_PHYSICAL_RETURN_CONFLICT,
            MoySkladConflict.status == "open",
        )
        .first()
    )
    if existing is not None:
        existing.sku = variant.sku
        existing.message = message
    else:
        db.add(
            MoySkladConflict(
                moysklad_id=variant.provider_id,
                sku=variant.sku,
                conflict_type=_STALE_PHYSICAL_RETURN_CONFLICT,
                message=message,
                status="open",
            )
        )

    reconciliation = (
        db.query(StockReconciliationLog)
        .filter(
            StockReconciliationLog.variant_id == variant.variant_id,
            StockReconciliationLog.action == _BLOCKED_RECONCILIATION_ACTION,
            StockReconciliationLog.status == "open",
        )
        .order_by(StockReconciliationLog.id.desc())
        .first()
    )
    if reconciliation is None:
        reconciliation = StockReconciliationLog(
            variant_id=variant.variant_id,
            sku=variant.sku,
            local_stock_qty=variant.stock_qty,
            external_stock_qty=int(external_stock),
            local_reserved_qty=variant.reserved_qty,
            action=_BLOCKED_RECONCILIATION_ACTION,
            status="open",
            message=message,
        )
        db.add(reconciliation)
    else:
        reconciliation.local_stock_qty = variant.stock_qty
        reconciliation.external_stock_qty = int(external_stock)
        reconciliation.local_reserved_qty = variant.reserved_qty
        reconciliation.message = message


def _uses_process_local_sqlite_database(db: Session) -> bool:
    """Return True where a second session cannot provide an independent commit."""

    bind = db.get_bind()
    if bind.dialect.name != "sqlite":
        return False
    database = getattr(getattr(bind, "url", None), "database", None)
    return database in (None, "", ":memory:")


def _is_open_evidence_unique_race(exc: IntegrityError) -> bool:
    """Recognize only the two expected first-writer-wins evidence races."""

    original = getattr(exc, "orig", None)
    diagnostic = getattr(original, "diag", None)
    constraint_name = getattr(diagnostic, "constraint_name", None)
    if constraint_name in _OPEN_EVIDENCE_CONSTRAINTS:
        return True

    # SQLite deterministic tests report columns rather than index names. Keep
    # this fallback narrow so unrelated integrity failures still fail closed.
    message = str(original or exc).lower()
    return (
        "unique constraint failed: moysklad_conflicts.moysklad_id, moysklad_conflicts.conflict_type"
        in message
        or "unique constraint failed: stock_reconciliation_logs.variant_id" in message
    )


def _persist_blocked_evidence_durably(
    db: Session,
    *,
    variant: _VariantAuthoritySnapshot,
    external_stock: int,
    pending_event_ids: tuple[int, ...],
    provider_exported: bool,
    catchup_floor: int | None,
) -> None:
    """Commit fail-closed operational evidence outside the business transaction.

    A blocked provider snapshot must remain explainable even if the caller rolls
    back its business transaction. Only the conflict/reconciliation evidence is
    written here; ProductVariant, order and return state stay owned by the
    caller's transaction. In-memory SQLite cannot create an independent durable
    transaction, so deterministic unit tests retain the legacy same-session
    path while production databases and file-backed rollback tests use a truly
    separate connection/commit.

    The first open row is protected by partial unique indexes. Concurrent stale
    snapshots may therefore race on the initial insert; the loser rolls back
    only its evidence transaction, rereads the winning open row and refreshes
    it. No ProductVariant/SKU lock is introduced and unrelated variants remain
    independently concurrent.
    """

    if _uses_process_local_sqlite_database(db):
        _open_or_refresh_conflict(
            db,
            variant=variant,
            external_stock=external_stock,
            pending_event_ids=pending_event_ids,
            provider_exported=provider_exported,
            catchup_floor=catchup_floor,
        )
        return

    bind = db.get_bind()
    EvidenceSession = sessionmaker(
        bind=bind,
        autocommit=False,
        autoflush=False,
        expire_on_commit=False,
    )
    for attempt in range(2):
        evidence_db = EvidenceSession()
        try:
            _open_or_refresh_conflict(
                evidence_db,
                variant=variant,
                external_stock=external_stock,
                pending_event_ids=pending_event_ids,
                provider_exported=provider_exported,
                catchup_floor=catchup_floor,
            )
            evidence_db.commit()
            return
        except IntegrityError as exc:
            evidence_db.rollback()
            if attempt == 0 and _is_open_evidence_unique_race(exc):
                continue
            raise
        except Exception:
            evidence_db.rollback()
            raise
        finally:
            evidence_db.close()

    raise RuntimeError("MoySklad blocked evidence persistence retry exhausted")


def _resolve_stale_conflicts(db: Session, variant: ProductVariant) -> None:
    provider_id = _provider_identity(variant)
    for row in (
        db.query(MoySkladConflict)
        .filter(
            MoySkladConflict.moysklad_id == provider_id,
            MoySkladConflict.conflict_type == _STALE_PHYSICAL_RETURN_CONFLICT,
            MoySkladConflict.status == "open",
        )
        .all()
    ):
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
                f"MoySklad physical return transaction and inbound stock caught up "
                f"for resalable inspection event(s) {event_text}"
            ),
        )
    )
    for row in (
        db.query(StockReconciliationLog)
        .filter(
            StockReconciliationLog.variant_id == int(variant.id),
            StockReconciliationLog.action == _BLOCKED_RECONCILIATION_ACTION,
            StockReconciliationLog.status == "open",
        )
        .all()
    ):
        row.status = "resolved"


def evaluate_moysklad_stock_snapshot(
    db: Session,
    variant: ProductVariant,
    external_stock: int,
) -> MoySkladStockDecision:
    """Protect warehouse-confirmed sellable returns from stale absolute sync.

    While resalable physical-return evidence is not fully reconciled, *no*
    differing absolute MoySklad snapshot may overwrite the local variant. The
    guard clears only when every pending physical case has a successful
    provider SalesReturn transaction and the raw inbound stock reaches the
    immutable post-return stock recorded by the local inventory ledger.

    This deliberately fails closed for mixed damaged/quarantine cases whose
    provider command is ``review_required``. Financial refunds and non-sellable
    dispositions never create this protection because they create no resalable
    inspection event.

    Blocked evidence is committed independently so a later business rollback
    cannot erase the reason the provider snapshot was rejected. Successful
    catch-up/resolution remains in the caller transaction and therefore rolls
    back atomically with any stock mutation it authorizes.
    """

    normalized_external = int(external_stock)
    if normalized_external < 0:
        raise ValueError("MoySklad stock cannot be negative")
    current_stock = int(variant.stock_qty or 0)
    reserved_qty = int(variant.reserved_qty or 0)
    target_stock = max(normalized_external, reserved_qty)
    evidence = _pending_resalable_evidence(db, int(variant.id))

    if not evidence:
        _resolve_stale_conflicts(db, variant)
        return MoySkladStockDecision(target_stock=target_stock, blocked=False)

    pending_event_ids = tuple(item.event_id for item in evidence)
    provider_exported = _provider_returns_exported(db, evidence)
    catchup_floor = _physical_ledger_floor(
        db,
        variant_id=int(variant.id),
        evidence=evidence,
    )
    if (
        provider_exported
        and catchup_floor is not None
        and normalized_external >= catchup_floor
    ):
        _record_catchup(
            db,
            variant=variant,
            external_stock=normalized_external,
            pending_event_ids=pending_event_ids,
        )
        _resolve_stale_conflicts(db, variant)
        return MoySkladStockDecision(target_stock=target_stock, blocked=False)

    _persist_blocked_evidence_durably(
        db,
        variant=_variant_authority_snapshot(variant),
        external_stock=normalized_external,
        pending_event_ids=pending_event_ids,
        provider_exported=provider_exported,
        catchup_floor=catchup_floor,
    )
    return MoySkladStockDecision(
        target_stock=current_stock,
        blocked=True,
        pending_event_ids=pending_event_ids,
    )
