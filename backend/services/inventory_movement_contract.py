from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Iterable

from sqlalchemy.orm import Session

from ..models import InventoryMovement
from ..reverse_logistics_models import (
    ReturnLogisticsCase,
    ReturnLogisticsEvent,
    ReturnLogisticsItem,
)

# Inventory authority follows the commercial lifecycle, not payment/refund
# wording. Canonical order states are grouped by the stock side effect they
# prove:
# - pending payment keeps the checkout reservation;
# - payment review/cancellation releases it without a sale;
# - settled/fulfillment/refund states keep the original sale committed.
# Financial refund states never imply a sellable physical return.
_COMMITTED_ORDER_STATUSES = {
    "paid",
    "assembling",
    "picking",  # legacy fulfillment alias retained for historical evidence
    "packed",  # legacy fulfillment alias retained for historical evidence
    "ready",
    "shipped",
    "completed",
    "refund_requested",
    "refund_pending",  # legacy/refund worker aliases retained for evidence
    "refund_retry_required",
    "refund_review_required",
    "partially_refunded",
    "refunded",
}
_RELEASED_RESERVATION_ORDER_STATUSES = {
    "payment_review_required",
    "cancelled",
    "expired",  # legacy reservation-expiry alias
}
_PENDING_ORDER_STATUSES = {
    "created",
    "payment_created",
    "pending",  # legacy aliases retained for historical evidence
    "pending_payment",
    "payment_pending",
}
_MOVEMENT_KINDS = {"reserve", "release", "commit", "return"}


@dataclass(frozen=True)
class PhysicalReturnEvidence:
    event_id: int
    order_id: int
    variant_id: int
    quantity: int

    @property
    def source(self) -> str:
        return f"reverse_logistics_event:{self.event_id}"


def expected_core_chain(order_status: str) -> tuple[str, ...] | None:
    status = str(order_status or "").strip().lower()
    if status in _RELEASED_RESERVATION_ORDER_STATUSES:
        return ("reserve", "release")
    if status in _COMMITTED_ORDER_STATUSES:
        return ("reserve", "commit")
    if status in _PENDING_ORDER_STATUSES:
        return ("reserve",)
    return None


def load_resalable_return_evidence(
    db: Session,
    order_ids: Iterable[int],
) -> dict[tuple[int, int], tuple[PhysicalReturnEvidence, ...]]:
    """Load the only durable evidence allowed to create sellable return stock.

    Financial state is intentionally absent from this query. A sellable return
    exists only when a persisted physical inspection event says `resalable` and
    points through its physical item/case back to the original order/variant.
    """

    normalized = sorted({int(order_id) for order_id in order_ids})
    if not normalized:
        return {}
    rows = (
        db.query(
            ReturnLogisticsEvent.id,
            ReturnLogisticsCase.order_id,
            ReturnLogisticsItem.variant_id,
            ReturnLogisticsEvent.quantity,
        )
        .join(
            ReturnLogisticsItem,
            ReturnLogisticsItem.id == ReturnLogisticsEvent.item_id,
        )
        .join(
            ReturnLogisticsCase,
            ReturnLogisticsCase.id == ReturnLogisticsEvent.case_id,
        )
        .filter(
            ReturnLogisticsCase.order_id.in_(normalized),
            ReturnLogisticsItem.case_id == ReturnLogisticsEvent.case_id,
            ReturnLogisticsEvent.event_type == "inspected",
            ReturnLogisticsEvent.disposition == "resalable",
            ReturnLogisticsEvent.quantity > 0,
        )
        .order_by(ReturnLogisticsEvent.id.asc())
        .all()
    )
    grouped: dict[tuple[int, int], list[PhysicalReturnEvidence]] = defaultdict(list)
    for event_id, order_id, variant_id, quantity in rows:
        evidence = PhysicalReturnEvidence(
            event_id=int(event_id),
            order_id=int(order_id),
            variant_id=int(variant_id),
            quantity=int(quantity),
        )
        grouped[(evidence.order_id, evidence.variant_id)].append(evidence)
    return {key: tuple(value) for key, value in grouped.items()}


def movement_transition_valid(movement: InventoryMovement) -> bool:
    quantity = int(movement.quantity)
    stock_before = int(movement.stock_before)
    stock_after = int(movement.stock_after)
    reserved_before = int(movement.reserved_before)
    reserved_after = int(movement.reserved_after)
    if quantity <= 0 or min(stock_before, stock_after, reserved_before, reserved_after) < 0:
        return False
    if reserved_before > stock_before or reserved_after > stock_after:
        return False
    if movement.kind == "reserve":
        return stock_after == stock_before and reserved_after == reserved_before + quantity
    if movement.kind == "release":
        return stock_after == stock_before and reserved_after == reserved_before - quantity
    if movement.kind == "commit":
        return (
            stock_after == stock_before - quantity
            and reserved_after == reserved_before - quantity
        )
    if movement.kind == "return":
        return stock_after == stock_before + quantity and reserved_after == reserved_before
    return False


def validate_variant_movement_chain(
    chain: list[InventoryMovement],
    *,
    order_quantity: int,
    core_chain: tuple[str, ...] | None,
    physical_returns: tuple[PhysicalReturnEvidence, ...] = (),
) -> tuple[str, ...]:
    """Validate one order/variant ledger against commercial and physical truth.

    Return movements are not inferred from money. They must be an exact
    one-for-one projection of durable resalable inspection events, including
    source and quantity. Multiple partial return events are therefore valid.

    Snapshot continuity is deliberately *not* required between two movements
    belonging to the same order. Other orders and explicit stock adjustments
    may legally touch the same variant between reserve, commit and return. Each
    movement still has to conserve its own stock/reserved transition exactly.
    """

    failures: list[str] = []
    expected_quantity = int(order_quantity)
    if expected_quantity <= 0:
        return ("order_quantity_invalid",)
    if core_chain is None:
        return ("order_status_unsupported",)
    if not chain:
        return ("movement_chain_missing",)

    kinds = tuple(str(movement.kind) for movement in chain)
    core_len = len(core_chain)
    if kinds[:core_len] != core_chain or any(kind != "return" for kind in kinds[core_len:]):
        failures.append("movement_sequence_invalid")

    core_movements = chain[:core_len]
    if len(core_movements) != core_len or any(
        int(movement.quantity) != expected_quantity for movement in core_movements
    ):
        failures.append("core_quantity_mismatch")

    invalid_transition_kinds: list[str] = []
    for movement in chain:
        if movement_transition_valid(movement):
            continue
        kind = str(movement.kind or "").strip().lower()
        if kind in _MOVEMENT_KINDS:
            invalid_transition_kinds.append(kind)
    if invalid_transition_kinds or any(
        str(movement.kind or "").strip().lower() not in _MOVEMENT_KINDS
        for movement in chain
    ):
        failures.append("movement_transition_invalid")
        failures.extend(
            f"{kind}_transition_invalid" for kind in invalid_transition_kinds
        )

    actual_returns = [movement for movement in chain if movement.kind == "return"]
    expected_returns = {evidence.source: evidence.quantity for evidence in physical_returns}
    actual_return_map: dict[str, int] = {}
    duplicate_source = False
    for movement in actual_returns:
        source = str(movement.source or "")
        if source in actual_return_map:
            duplicate_source = True
        actual_return_map[source] = int(movement.quantity)
    if duplicate_source:
        failures.append("return_source_duplicate")
    if actual_return_map != expected_returns:
        failures.append("physical_return_evidence_mismatch")

    expected_return_qty = sum(expected_returns.values())
    actual_return_qty = sum(int(movement.quantity) for movement in actual_returns)
    if expected_return_qty > expected_quantity or actual_return_qty > expected_quantity:
        failures.append("return_quantity_exceeds_commit")

    if physical_returns and "commit" not in core_chain:
        failures.append("return_without_commit")

    return tuple(dict.fromkeys(failures))
