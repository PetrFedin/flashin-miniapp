from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable

from sqlalchemy import func
from sqlalchemy.orm import Session

from ..models import Order, OrderItem, ReturnRequest
from ..refund_allocation_models import ReturnRefundAllocation
from ..reverse_logistics_models import ReturnLogisticsCase
from .order_money_allocation import (
    ORDER_MONEY_POLICY_VERSION,
    OrderMoneyAllocationError,
    allocate_order_money,
    money_cents,
)

_FINAL_REFUND_STATUSES = {"approved", "approved_partial"}
_RESERVING_REFUND_STATUSES = {
    "processing",
    "refund_retry_required",
    "refund_review_required",
    "refund_pending",
    "approved",
    "approved_partial",
}


class RefundAllocationError(ValueError):
    """Refund financial evidence is invalid or exceeds original paid value."""


@dataclass(frozen=True)
class RefundAllocationSpec:
    component_kind: str
    amount_cents: int
    order_item_id: int | None = None
    quantity_evidence: int | None = None

    @property
    def component_key(self) -> str:
        if self.component_kind == "item":
            return f"item:{int(self.order_item_id or 0)}"
        return self.component_kind


def _order_items(db: Session, order_id: int) -> list[OrderItem]:
    rows = (
        db.query(OrderItem)
        .filter(OrderItem.order_id == order_id)
        .order_by(OrderItem.id.asc())
        .all()
    )
    if not rows:
        raise RefundAllocationError("Order has no merchandise lines")
    return rows


def _policy(db: Session, order: Order):
    items = _order_items(db, int(order.id))
    try:
        allocation = allocate_order_money(order, items)
    except OrderMoneyAllocationError as exc:
        raise RefundAllocationError(str(exc)) from exc
    return allocation, items


def _active_allocation_rows(
    db: Session,
    order_id: int,
    *,
    exclude_return_id: int | None = None,
) -> list[ReturnRefundAllocation]:
    query = (
        db.query(ReturnRefundAllocation)
        .join(
            ReturnRequest,
            ReturnRequest.id == ReturnRefundAllocation.return_request_id,
        )
        .filter(
            ReturnRefundAllocation.order_id == order_id,
            ReturnRequest.status.in_(_RESERVING_REFUND_STATUSES),
        )
    )
    if exclude_return_id is not None:
        query = query.filter(ReturnRefundAllocation.return_request_id != exclude_return_id)
    return query.order_by(ReturnRefundAllocation.id.asc()).all()


def _component_usage(
    rows: Iterable[ReturnRefundAllocation],
) -> tuple[dict[int, int], dict[int, int], int, int]:
    item_cents: dict[int, int] = {}
    item_quantities: dict[int, int] = {}
    delivery_cents = 0
    goodwill_cents = 0
    for row in rows:
        amount = int(row.amount_cents)
        if row.component_kind == "item":
            item_id = int(row.order_item_id or 0)
            item_cents[item_id] = item_cents.get(item_id, 0) + amount
            if row.quantity_evidence is not None:
                item_quantities[item_id] = (
                    item_quantities.get(item_id, 0) + int(row.quantity_evidence)
                )
        elif row.component_kind == "delivery":
            delivery_cents += amount
        elif row.component_kind == "goodwill":
            goodwill_cents += amount
        else:
            raise RefundAllocationError("Stored refund allocation kind is invalid")
    return item_cents, item_quantities, delivery_cents, goodwill_cents


def _normalize_spec(raw) -> RefundAllocationSpec:
    kind = str(getattr(raw, "component_kind", "") or "").strip().lower()
    if kind not in {"item", "delivery", "goodwill"}:
        raise RefundAllocationError("Refund allocation component is invalid")
    try:
        amount_cents = money_cents(getattr(raw, "amount", None), "refund allocation amount")
    except OrderMoneyAllocationError as exc:
        raise RefundAllocationError(str(exc)) from exc
    if amount_cents <= 0:
        raise RefundAllocationError("Refund allocation amount must be positive")

    raw_item_id = getattr(raw, "order_item_id", None)
    raw_quantity = getattr(raw, "quantity_evidence", None)
    if kind == "item":
        if isinstance(raw_item_id, bool) or not isinstance(raw_item_id, int) or raw_item_id <= 0:
            raise RefundAllocationError("Item refund allocation requires order_item_id")
        quantity = None
        if raw_quantity is not None:
            if isinstance(raw_quantity, bool) or not isinstance(raw_quantity, int) or raw_quantity <= 0:
                raise RefundAllocationError("Refund allocation quantity evidence must be positive")
            quantity = int(raw_quantity)
        return RefundAllocationSpec(
            component_kind="item",
            order_item_id=int(raw_item_id),
            quantity_evidence=quantity,
            amount_cents=amount_cents,
        )

    if raw_item_id is not None or raw_quantity is not None:
        raise RefundAllocationError(
            "Delivery/goodwill allocation cannot contain item identity or quantity"
        )
    return RefundAllocationSpec(component_kind=kind, amount_cents=amount_cents)


def _persisted_specs(
    db: Session,
    return_request_id: int,
) -> list[RefundAllocationSpec]:
    rows = (
        db.query(ReturnRefundAllocation)
        .filter(ReturnRefundAllocation.return_request_id == return_request_id)
        .order_by(ReturnRefundAllocation.component_key.asc())
        .all()
    )
    return [
        RefundAllocationSpec(
            component_kind=row.component_kind,
            order_item_id=row.order_item_id,
            quantity_evidence=row.quantity_evidence,
            amount_cents=int(row.amount_cents),
        )
        for row in rows
    ]


def _spec_fingerprint(spec: RefundAllocationSpec) -> tuple:
    return (
        spec.component_key,
        spec.component_kind,
        int(spec.order_item_id or 0),
        int(spec.quantity_evidence or 0),
        int(spec.amount_cents),
    )


def _validate_specs(
    db: Session,
    *,
    order: Order,
    ret: ReturnRequest,
    requested_cents: int,
    specs: list[RefundAllocationSpec],
) -> None:
    if not specs:
        raise RefundAllocationError("Refund allocation is required")
    if sum(spec.amount_cents for spec in specs) != requested_cents:
        raise RefundAllocationError("Refund allocation total must equal approved refund amount")

    keys = [spec.component_key for spec in specs]
    if len(set(keys)) != len(keys):
        raise RefundAllocationError("Refund allocation contains duplicate components")

    policy, items = _policy(db, order)
    item_by_id = {int(item.id): item for item in items}
    line_capacity = policy.line_cents()
    prior = _active_allocation_rows(
        db,
        int(order.id),
        exclude_return_id=int(ret.id),
    )
    prior_item_cents, prior_quantities, prior_delivery, _ = _component_usage(prior)

    for spec in specs:
        if spec.component_kind == "item":
            item_id = int(spec.order_item_id or 0)
            item = item_by_id.get(item_id)
            if item is None:
                raise RefundAllocationError("Refund allocation item is not part of the order")
            remaining_cents = int(line_capacity[item_id]) - int(prior_item_cents.get(item_id, 0))
            if spec.amount_cents > remaining_cents:
                raise RefundAllocationError(
                    f"Refund allocation exceeds remaining value for order item {item_id}"
                )
            if spec.quantity_evidence is not None:
                remaining_qty = int(item.quantity) - int(prior_quantities.get(item_id, 0))
                if spec.quantity_evidence > remaining_qty:
                    raise RefundAllocationError(
                        f"Refund quantity evidence exceeds remaining sold quantity for order item {item_id}"
                    )
        elif spec.component_kind == "delivery":
            if spec.amount_cents > policy.delivery_cents - prior_delivery:
                raise RefundAllocationError("Refund allocation exceeds remaining delivery value")

    prior_total = sum(int(row.amount_cents) for row in prior)
    if prior_total + requested_cents > policy.order_total_cents:
        raise RefundAllocationError("Refund allocations exceed original paid order total")


def _automatic_full_remaining_specs(
    db: Session,
    *,
    order: Order,
    ret: ReturnRequest,
    requested_cents: int,
) -> list[RefundAllocationSpec]:
    policy, _items = _policy(db, order)
    prior = _active_allocation_rows(
        db,
        int(order.id),
        exclude_return_id=int(ret.id),
    )
    prior_item_cents, _prior_quantities, prior_delivery, _prior_goodwill = _component_usage(prior)

    specs: list[RefundAllocationSpec] = []
    for line in policy.lines:
        remaining = int(line.net_cents) - int(prior_item_cents.get(line.order_item_id, 0))
        if remaining < 0:
            raise RefundAllocationError("Stored item allocations exceed original line value")
        if remaining:
            specs.append(
                RefundAllocationSpec(
                    component_kind="item",
                    order_item_id=int(line.order_item_id),
                    quantity_evidence=None,
                    amount_cents=remaining,
                )
            )
    remaining_delivery = int(policy.delivery_cents) - int(prior_delivery)
    if remaining_delivery < 0:
        raise RefundAllocationError("Stored delivery allocations exceed original delivery value")
    if remaining_delivery:
        specs.append(
            RefundAllocationSpec(
                component_kind="delivery",
                amount_cents=remaining_delivery,
            )
        )

    allocatable = sum(spec.amount_cents for spec in specs)
    if allocatable != requested_cents:
        raise RefundAllocationError(
            "Partial or goodwill refund requires explicit item/delivery/goodwill allocation"
        )
    return specs


def ensure_refund_allocation(
    db: Session,
    *,
    order: Order,
    ret: ReturnRequest,
    requested_amount: object,
    raw_allocations: Iterable | None,
) -> dict[str, object]:
    """Persist immutable financial composition before provider refund execution.

    The caller must already hold the canonical Order -> ReturnRequest locks.
    """

    try:
        requested_cents = money_cents(requested_amount, "refund amount")
    except OrderMoneyAllocationError as exc:
        raise RefundAllocationError(str(exc)) from exc
    if requested_cents <= 0:
        raise RefundAllocationError("Refund amount must be positive")

    persisted = _persisted_specs(db, int(ret.id))
    incoming = [_normalize_spec(raw) for raw in (raw_allocations or [])]

    if persisted:
        expected = sorted((_spec_fingerprint(spec) for spec in persisted))
        if incoming:
            actual = sorted((_spec_fingerprint(spec) for spec in incoming))
            if actual != expected:
                raise RefundAllocationError(
                    "Refund allocation is already fixed for this request"
                )
        if sum(spec.amount_cents for spec in persisted) != requested_cents:
            raise RefundAllocationError(
                "Stored refund allocation does not match fixed refund amount"
            )
        _validate_specs(
            db,
            order=order,
            ret=ret,
            requested_cents=requested_cents,
            specs=persisted,
        )
        return allocation_summary(persisted, requested_cents)

    specs = incoming or _automatic_full_remaining_specs(
        db,
        order=order,
        ret=ret,
        requested_cents=requested_cents,
    )
    _validate_specs(
        db,
        order=order,
        ret=ret,
        requested_cents=requested_cents,
        specs=specs,
    )

    for spec in specs:
        db.add(
            ReturnRefundAllocation(
                return_request_id=int(ret.id),
                order_id=int(order.id),
                order_item_id=spec.order_item_id,
                component_kind=spec.component_kind,
                component_key=spec.component_key,
                quantity_evidence=spec.quantity_evidence,
                amount_cents=int(spec.amount_cents),
                policy_version=ORDER_MONEY_POLICY_VERSION,
            )
        )
    db.flush()
    return allocation_summary(specs, requested_cents)


def validate_persisted_return_allocation(
    db: Session,
    *,
    order: Order,
    ret: ReturnRequest,
) -> dict[str, object]:
    specs = _persisted_specs(db, int(ret.id))
    if not specs:
        raise RefundAllocationError(
            "Refund cannot be finalized without financial allocation evidence"
        )
    try:
        expected_cents = money_cents(ret.refund_amount, "refund amount")
    except OrderMoneyAllocationError as exc:
        raise RefundAllocationError(str(exc)) from exc
    _validate_specs(
        db,
        order=order,
        ret=ret,
        requested_cents=expected_cents,
        specs=specs,
    )
    return allocation_summary(specs, expected_cents)


def allocation_summary(
    specs: Iterable[RefundAllocationSpec],
    requested_cents: int,
) -> dict[str, object]:
    rows = list(specs)
    item_cents = sum(spec.amount_cents for spec in rows if spec.component_kind == "item")
    delivery_cents = sum(
        spec.amount_cents for spec in rows if spec.component_kind == "delivery"
    )
    goodwill_cents = sum(
        spec.amount_cents for spec in rows if spec.component_kind == "goodwill"
    )
    return {
        "policy_version": ORDER_MONEY_POLICY_VERSION,
        "allocated_cents": int(requested_cents),
        "item_cents": int(item_cents),
        "delivery_cents": int(delivery_cents),
        "goodwill_cents": int(goodwill_cents),
        "components": [
            {
                "component_kind": spec.component_kind,
                "order_item_id": spec.order_item_id,
                "quantity_evidence": spec.quantity_evidence,
                "amount_cents": int(spec.amount_cents),
            }
            for spec in sorted(rows, key=lambda row: row.component_key)
        ],
    }


def refund_allocation_options(
    db: Session,
    *,
    order: Order,
    exclude_return_id: int | None = None,
) -> dict[str, object]:
    policy, items = _policy(db, order)
    active = _active_allocation_rows(
        db,
        int(order.id),
        exclude_return_id=exclude_return_id,
    )
    item_cents, quantities, delivery_cents, goodwill_cents = _component_usage(active)
    items_by_id = {int(item.id): item for item in items}

    return {
        "policy_version": ORDER_MONEY_POLICY_VERSION,
        "order_total_cents": int(policy.order_total_cents),
        "merchandise_cents": int(policy.merchandise_cents),
        "delivery_cents": int(policy.delivery_cents),
        "allocated_cents": sum(int(row.amount_cents) for row in active),
        "goodwill_allocated_cents": int(goodwill_cents),
        "delivery_remaining_cents": max(0, int(policy.delivery_cents) - int(delivery_cents)),
        "items": [
            {
                "order_item_id": int(line.order_item_id),
                "title": str(items_by_id[line.order_item_id].title),
                "size": str(items_by_id[line.order_item_id].size),
                "ordered_qty": int(line.quantity),
                "net_total_cents": int(line.net_cents),
                "allocated_cents": int(item_cents.get(line.order_item_id, 0)),
                "remaining_cents": max(
                    0,
                    int(line.net_cents) - int(item_cents.get(line.order_item_id, 0)),
                ),
                "quantity_evidence_allocated": int(
                    quantities.get(line.order_item_id, 0)
                ),
            }
            for line in policy.lines
        ],
    }


def return_request_allocation_summary(
    db: Session,
    return_request_id: int,
) -> dict[str, object]:
    specs = _persisted_specs(db, return_request_id)
    allocated = sum(spec.amount_cents for spec in specs)
    return allocation_summary(specs, allocated)


def _physical_value_by_item(
    db: Session,
    order_id: int,
) -> tuple[dict[int, int], int, bool]:
    """Return inspected physical value by original item using proven valuation."""

    # Import lazily to avoid coupling model import/bootstrap order to MoySklad code.
    from .moysklad_reverse_return import (
        MoySkladReviewRequired,
        _build_physical_return_allocation,
    )

    cases = (
        db.query(ReturnLogisticsCase)
        .filter(ReturnLogisticsCase.order_id == order_id)
        .order_by(ReturnLogisticsCase.id.asc())
        .all()
    )
    inspected = [case for case in cases if case.status == "inspected"]
    open_case_exists = any(case.status != "inspected" for case in cases)
    values: dict[int, int] = {}
    for case in inspected:
        try:
            allocation = _build_physical_return_allocation(
                db,
                int(case.id),
                lock_order=False,
            )
        except MoySkladReviewRequired as exc:
            raise RefundAllocationError(str(exc)) from exc
        raw_lines = allocation.get("lines")
        if not isinstance(raw_lines, list):
            raise RefundAllocationError("Physical return valuation lines are invalid")
        for raw in raw_lines:
            if not isinstance(raw, dict):
                raise RefundAllocationError("Physical return valuation line is invalid")
            item_id = int(raw.get("order_item_id") or 0)
            cents = int(raw.get("net_total_cents") or 0)
            if item_id <= 0 or cents < 0:
                raise RefundAllocationError("Physical return valuation evidence is invalid")
            values[item_id] = values.get(item_id, 0) + cents
    return values, len(cases), open_case_exists


def reconcile_refund_allocations(
    db: Session,
    order_id: int,
) -> dict[str, object]:
    """Compare completed financial item evidence with verified physical value."""

    order = db.query(Order).filter(Order.id == order_id).first()
    if order is None:
        return {
            "status": "BLOCKED",
            "codes": ["order_missing"],
            "order_id": int(order_id),
            "lines": [],
        }

    try:
        policy, _items = _policy(db, order)
        all_rows = (
            db.query(ReturnRefundAllocation, ReturnRequest)
            .join(
                ReturnRequest,
                ReturnRequest.id == ReturnRefundAllocation.return_request_id,
            )
            .filter(ReturnRefundAllocation.order_id == order_id)
            .order_by(ReturnRefundAllocation.id.asc())
            .all()
        )

        final_item: dict[int, int] = {}
        pending_allocation = False
        by_return: dict[int, int] = {}
        delivery_final = 0
        goodwill_final = 0
        for row, ret in all_rows:
            if int(row.policy_version) != ORDER_MONEY_POLICY_VERSION:
                raise RefundAllocationError("Refund allocation policy version is unsupported")
            if int(ret.order_id) != int(order_id):
                raise RefundAllocationError("Refund allocation return ownership mismatch")
            by_return[int(ret.id)] = by_return.get(int(ret.id), 0) + int(row.amount_cents)
            if ret.status in _FINAL_REFUND_STATUSES:
                if row.component_kind == "item":
                    item_id = int(row.order_item_id or 0)
                    final_item[item_id] = final_item.get(item_id, 0) + int(row.amount_cents)
                elif row.component_kind == "delivery":
                    delivery_final += int(row.amount_cents)
                elif row.component_kind == "goodwill":
                    goodwill_final += int(row.amount_cents)
                else:
                    raise RefundAllocationError("Refund allocation kind is invalid")
            elif ret.status in _RESERVING_REFUND_STATUSES:
                pending_allocation = True

        final_returns = (
            db.query(ReturnRequest)
            .filter(
                ReturnRequest.order_id == order_id,
                ReturnRequest.status.in_(_FINAL_REFUND_STATUSES),
            )
            .order_by(ReturnRequest.id.asc())
            .all()
        )
        codes: list[str] = []
        for ret in final_returns:
            expected = money_cents(ret.refund_amount, "completed refund amount")
            actual = int(by_return.get(int(ret.id), 0))
            if expected != actual:
                codes.append(f"return_{ret.id}_allocation_total_mismatch")

        line_capacity = policy.line_cents()
        for item_id, amount in final_item.items():
            if item_id not in line_capacity or amount > int(line_capacity[item_id]):
                codes.append(f"item_{item_id}_financial_overallocation")
        if delivery_final > int(policy.delivery_cents):
            codes.append("delivery_financial_overallocation")

        final_refund_cents = sum(
            money_cents(ret.refund_amount, "completed refund amount")
            for ret in final_returns
        )
        final_allocation_cents = sum(final_item.values()) + delivery_final + goodwill_final
        if final_refund_cents != final_allocation_cents:
            codes.append("completed_refund_allocation_total_mismatch")
        if final_allocation_cents > int(policy.order_total_cents):
            codes.append("financial_allocation_exceeds_order_total")

        open_return_exists = (
            db.query(ReturnRequest.id)
            .filter(
                ReturnRequest.order_id == order_id,
                ReturnRequest.status.in_(
                    [
                        "requested",
                        "processing",
                        "refund_retry_required",
                        "refund_review_required",
                        "refund_pending",
                    ]
                ),
            )
            .first()
            is not None
        )
        pending_allocation = pending_allocation or open_return_exists

        physical_item, physical_case_count, open_case_exists = _physical_value_by_item(
            db,
            int(order_id),
        )
        all_item_ids = sorted(set(line_capacity) | set(final_item) | set(physical_item))
        lines = []
        mismatch = False
        financial_exceeds_physical = False
        physical_exceeds_financial = False
        for item_id in all_item_ids:
            financial = int(final_item.get(item_id, 0))
            physical = int(physical_item.get(item_id, 0))
            delta = financial - physical
            if delta:
                mismatch = True
                if delta > 0:
                    financial_exceeds_physical = True
                else:
                    physical_exceeds_financial = True
            lines.append(
                {
                    "order_item_id": int(item_id),
                    "financial_cents": financial,
                    "physical_cents": physical,
                    "delta_cents": delta,
                }
            )

        if codes:
            status = "BLOCKED"
        elif mismatch:
            if pending_allocation or open_case_exists or (
                financial_exceeds_physical and physical_case_count == 0
            ):
                status = "PENDING"
            else:
                status = "REVIEW"
                if physical_exceeds_financial:
                    codes.append("physical_value_exceeds_completed_item_refunds")
                if financial_exceeds_physical:
                    codes.append("completed_item_refunds_exceed_physical_value")
        elif pending_allocation or open_case_exists:
            status = "PENDING"
        else:
            status = "PASS"

        return {
            "status": status,
            "codes": codes,
            "order_id": int(order_id),
            "policy_version": ORDER_MONEY_POLICY_VERSION,
            "completed_refund_cents": int(final_refund_cents),
            "completed_item_cents": int(sum(final_item.values())),
            "completed_delivery_cents": int(delivery_final),
            "completed_goodwill_cents": int(goodwill_final),
            "physical_item_cents": int(sum(physical_item.values())),
            "pending_allocation": bool(pending_allocation),
            "open_physical_case": bool(open_case_exists),
            "lines": lines,
        }
    except (OrderMoneyAllocationError, RefundAllocationError, ValueError) as exc:
        return {
            "status": "BLOCKED",
            "codes": ["allocation_evidence_invalid"],
            "detail": str(exc)[:255],
            "order_id": int(order_id),
            "lines": [],
        }
