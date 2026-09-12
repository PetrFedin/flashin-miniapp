from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ..database import utcnow_naive
from ..models import InventoryMovement, Order, ProductVariant, ReturnRequest
from ..reverse_logistics_models import (
    ReturnLogisticsCase,
    ReturnLogisticsEvent,
    ReturnLogisticsItem,
)

_IDEMPOTENCY_RE = re.compile(r"^[A-Za-z0-9_.:-]{8,128}$")
_DISPOSITIONS = frozenset({"resalable", "damaged", "quarantine"})


@dataclass(frozen=True)
class PhysicalMutationResult:
    case: ReturnLogisticsCase
    item: ReturnLogisticsItem
    event: ReturnLogisticsEvent
    idempotent: bool


def _key(value: str) -> str:
    key = str(value or "").strip()
    if not _IDEMPOTENCY_RE.fullmatch(key):
        raise HTTPException(status_code=400, detail="Idempotency-Key must contain 8-128 safe characters")
    return key


def _payload_hash(event_type: str, quantity: int, disposition: str = "") -> str:
    payload = json.dumps(
        {"event_type": event_type, "quantity": int(quantity), "disposition": disposition},
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _existing_event(
    db: Session,
    *,
    item_id: int,
    idempotency_key: str,
    expected_hash: str,
) -> ReturnLogisticsEvent | None:
    event = (
        db.query(ReturnLogisticsEvent)
        .filter(
            ReturnLogisticsEvent.item_id == item_id,
            ReturnLogisticsEvent.idempotency_key == idempotency_key,
        )
        .first()
    )
    if event is not None and event.payload_hash != expected_hash:
        raise HTTPException(status_code=409, detail="Idempotency-Key was reused with different physical return data")
    return event


def ensure_physical_case(
    db: Session,
    *,
    ret: ReturnRequest,
    order: Order,
) -> ReturnLogisticsCase:
    if int(ret.order_id) != int(order.id) or int(ret.customer_id) != int(order.customer_id):
        raise HTTPException(status_code=409, detail="Return request/order ownership mismatch")

    case = (
        db.query(ReturnLogisticsCase)
        .filter(ReturnLogisticsCase.return_request_id == ret.id)
        .with_for_update()
        .first()
    )
    if case is not None:
        return case
    if not order.items:
        raise HTTPException(status_code=409, detail="Order has no physical return items")

    case = ReturnLogisticsCase(
        return_request_id=ret.id,
        order_id=order.id,
        customer_id=order.customer_id,
        status="requested",
        created_at=utcnow_naive(),
        updated_at=utcnow_naive(),
    )
    db.add(case)
    db.flush()
    for order_item in order.items:
        if int(order_item.quantity) <= 0:
            raise HTTPException(status_code=409, detail="Order item has invalid quantity")
        db.add(
            ReturnLogisticsItem(
                case_id=case.id,
                order_item_id=order_item.id,
                variant_id=order_item.variant_id,
                ordered_qty=int(order_item.quantity),
            )
        )
    db.flush()
    return case


def _locked_item(db: Session, case_id: int, item_id: int) -> ReturnLogisticsItem:
    item = (
        db.query(ReturnLogisticsItem)
        .filter(ReturnLogisticsItem.id == item_id, ReturnLogisticsItem.case_id == case_id)
        .with_for_update()
        .first()
    )
    if item is None:
        raise HTTPException(status_code=404, detail="Physical return item not found")
    return item


def _refresh_case(case: ReturnLogisticsCase, items: list[ReturnLogisticsItem]) -> None:
    authorized = sum(int(row.authorized_qty) for row in items)
    received = sum(int(row.received_qty) for row in items)
    inspected = sum(int(row.inspected_qty) for row in items)
    if authorized > 0 and inspected == authorized:
        case.status = "inspected"
    elif authorized > 0 and received == authorized:
        case.status = "received"
    elif case.status != "in_transit" and authorized > 0:
        case.status = "authorized"
    case.updated_at = utcnow_naive()


def _event(
    db: Session,
    *,
    case: ReturnLogisticsCase,
    item: ReturnLogisticsItem,
    event_type: str,
    quantity: int,
    disposition: str,
    idempotency_key: str,
    actor_admin_id: int | None,
    reason: str,
) -> tuple[ReturnLogisticsEvent, bool]:
    key = _key(idempotency_key)
    digest = _payload_hash(event_type, quantity, disposition)
    existing = _existing_event(
        db,
        item_id=item.id,
        idempotency_key=key,
        expected_hash=digest,
    )
    if existing is not None:
        return existing, True
    event = ReturnLogisticsEvent(
        case_id=case.id,
        item_id=item.id,
        event_type=event_type,
        disposition=disposition,
        quantity=quantity,
        idempotency_key=key,
        payload_hash=digest,
        actor_admin_id=actor_admin_id,
        reason=str(reason or "").strip()[:2000],
        created_at=utcnow_naive(),
    )
    db.add(event)
    db.flush()
    return event, False


def _refresh_case_from_db(db: Session, case: ReturnLogisticsCase) -> None:
    # SessionLocal intentionally uses autoflush=False. Explicitly persist item
    # counters before deriving case state from SQL so lifecycle projection and
    # subsequent lock steps observe the same transaction truth.
    db.flush()
    items = db.query(ReturnLogisticsItem).filter(ReturnLogisticsItem.case_id == case.id).all()
    _refresh_case(case, items)
    db.flush()


def authorize_item(
    db: Session,
    *,
    case_id: int,
    item_id: int,
    quantity: int,
    idempotency_key: str,
    actor_admin_id: int | None,
    reason: str = "",
) -> PhysicalMutationResult:
    case = db.query(ReturnLogisticsCase).filter(ReturnLogisticsCase.id == case_id).with_for_update().first()
    if case is None:
        raise HTTPException(status_code=404, detail="Physical return case not found")
    item = _locked_item(db, case.id, item_id)
    if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity <= 0 or quantity > item.ordered_qty:
        raise HTTPException(status_code=409, detail="Authorized quantity is outside ordered quantity")
    event, idempotent = _event(
        db,
        case=case,
        item=item,
        event_type="authorized",
        quantity=quantity,
        disposition="",
        idempotency_key=idempotency_key,
        actor_admin_id=actor_admin_id,
        reason=reason,
    )
    if not idempotent:
        if item.authorized_qty:
            raise HTTPException(status_code=409, detail="Physical return item is already authorized")
        item.authorized_qty = quantity
        _refresh_case_from_db(db, case)
    return PhysicalMutationResult(case, item, event, idempotent)


def mark_in_transit(db: Session, *, case_id: int) -> ReturnLogisticsCase:
    db.flush()
    case = db.query(ReturnLogisticsCase).filter(ReturnLogisticsCase.id == case_id).with_for_update().first()
    if case is None:
        raise HTTPException(status_code=404, detail="Physical return case not found")
    authorized = (
        db.query(ReturnLogisticsItem)
        .filter(ReturnLogisticsItem.case_id == case.id, ReturnLogisticsItem.authorized_qty > 0)
        .count()
    )
    if not authorized or case.status not in {"authorized", "in_transit"}:
        raise HTTPException(status_code=409, detail="Only an authorized physical return can enter transit")
    case.status = "in_transit"
    case.updated_at = utcnow_naive()
    db.flush()
    return case


def receive_item(
    db: Session,
    *,
    case_id: int,
    item_id: int,
    quantity: int,
    idempotency_key: str,
    actor_admin_id: int | None,
    reason: str = "",
) -> PhysicalMutationResult:
    case = db.query(ReturnLogisticsCase).filter(ReturnLogisticsCase.id == case_id).with_for_update().first()
    if case is None:
        raise HTTPException(status_code=404, detail="Physical return case not found")
    item = _locked_item(db, case.id, item_id)
    if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity <= 0:
        raise HTTPException(status_code=400, detail="Received quantity must be positive")
    event, idempotent = _event(
        db,
        case=case,
        item=item,
        event_type="received",
        quantity=quantity,
        disposition="",
        idempotency_key=idempotency_key,
        actor_admin_id=actor_admin_id,
        reason=reason,
    )
    if not idempotent:
        if item.authorized_qty <= 0 or item.received_qty + quantity > item.authorized_qty:
            raise HTTPException(status_code=409, detail="Received quantity exceeds authorized physical return")
        item.received_qty += quantity
        _refresh_case_from_db(db, case)
    return PhysicalMutationResult(case, item, event, idempotent)


def inspect_item(
    db: Session,
    *,
    case_id: int,
    item_id: int,
    quantity: int,
    disposition: str,
    idempotency_key: str,
    actor_admin_id: int | None,
    reason: str = "",
) -> PhysicalMutationResult:
    normalized_disposition = str(disposition or "").strip().lower()
    if normalized_disposition not in _DISPOSITIONS:
        raise HTTPException(status_code=400, detail="Disposition must be resalable, damaged or quarantine")
    case = db.query(ReturnLogisticsCase).filter(ReturnLogisticsCase.id == case_id).with_for_update().first()
    if case is None:
        raise HTTPException(status_code=404, detail="Physical return case not found")
    item = _locked_item(db, case.id, item_id)
    if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity <= 0:
        raise HTTPException(status_code=400, detail="Inspected quantity must be positive")
    event, idempotent = _event(
        db,
        case=case,
        item=item,
        event_type="inspected",
        quantity=quantity,
        disposition=normalized_disposition,
        idempotency_key=idempotency_key,
        actor_admin_id=actor_admin_id,
        reason=reason,
    )
    if not idempotent:
        if item.inspected_qty + quantity > item.received_qty:
            raise HTTPException(status_code=409, detail="Inspected quantity exceeds physically received quantity")
        item.inspected_qty += quantity
        if normalized_disposition == "resalable":
            item.resalable_qty += quantity
            variant = (
                db.query(ProductVariant)
                .filter(ProductVariant.id == item.variant_id)
                .with_for_update()
                .first()
            )
            if variant is None:
                raise HTTPException(status_code=409, detail="Physical return variant no longer exists")
            variant.stock_qty += quantity
            db.add(
                InventoryMovement(
                    variant_id=item.variant_id,
                    order_id=case.order_id,
                    kind="return",
                    quantity=quantity,
                    source=f"reverse_logistics_event:{event.id}",
                )
            )
        elif normalized_disposition == "damaged":
            item.damaged_qty += quantity
        else:
            item.quarantine_qty += quantity
        _refresh_case_from_db(db, case)
    return PhysicalMutationResult(case, item, event, idempotent)


def physical_case_summary(db: Session, case: ReturnLogisticsCase) -> dict[str, object]:
    db.flush()
    items = (
        db.query(ReturnLogisticsItem)
        .filter(ReturnLogisticsItem.case_id == case.id)
        .order_by(ReturnLogisticsItem.id.asc())
        .all()
    )
    return {
        "id": case.id,
        "return_request_id": case.return_request_id,
        "order_id": case.order_id,
        "status": case.status,
        "items": [
            {
                "id": row.id,
                "order_item_id": row.order_item_id,
                "variant_id": row.variant_id,
                "ordered_qty": row.ordered_qty,
                "authorized_qty": row.authorized_qty,
                "received_qty": row.received_qty,
                "inspected_qty": row.inspected_qty,
                "resalable_qty": row.resalable_qty,
                "damaged_qty": row.damaged_qty,
                "quarantine_qty": row.quarantine_qty,
            }
            for row in items
        ],
    }
