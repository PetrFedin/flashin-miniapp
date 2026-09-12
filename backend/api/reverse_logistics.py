from __future__ import annotations

from pydantic import BaseModel, Field
from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Order, ReturnRequest
from ..reverse_logistics_models import ReturnLogisticsCase, ReturnLogisticsItem
from ..security import get_current_admin, get_current_customer
from ..services.audit import log_admin_action
from ..services.moysklad_reverse_return import enqueue_moysklad_physical_sales_return
from ..services.rbac import RETURNS_PHYSICAL_WRITE_PERMISSION, require_permission
from ..services.reverse_logistics import (
    authorize_item,
    ensure_physical_case,
    inspect_item,
    mark_in_transit,
    physical_case_summary,
    receive_item,
)

router = APIRouter(tags=["reverse-logistics"])


class PhysicalQuantityIn(BaseModel):
    order_item_id: int = Field(gt=0)
    quantity: int = Field(gt=0)
    reason: str = Field(default="", max_length=2000)


class PhysicalInspectionIn(PhysicalQuantityIn):
    disposition: str = Field(min_length=3, max_length=32)


def _return_snapshot(db: Session, return_id: int):
    return (
        db.query(ReturnRequest.id, ReturnRequest.order_id, ReturnRequest.customer_id)
        .filter(ReturnRequest.id == return_id)
        .first()
    )


def _lock_return_root(db: Session, return_id: int) -> tuple[ReturnRequest, Order]:
    snapshot = _return_snapshot(db, return_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="Return request not found")
    order = (
        db.query(Order)
        .filter(Order.id == int(snapshot.order_id))
        .with_for_update()
        .first()
    )
    if order is None:
        raise HTTPException(status_code=409, detail="Return request is linked to a missing order")
    ret = (
        db.query(ReturnRequest)
        .filter(ReturnRequest.id == return_id, ReturnRequest.order_id == order.id)
        .with_for_update()
        .first()
    )
    if ret is None or int(ret.customer_id) != int(order.customer_id):
        raise HTTPException(status_code=409, detail="Return request changed while being locked")
    return ret, order


def _case_item_by_order_item(db: Session, case_id: int, order_item_id: int) -> ReturnLogisticsItem:
    item = (
        db.query(ReturnLogisticsItem)
        .filter(
            ReturnLogisticsItem.case_id == case_id,
            ReturnLogisticsItem.order_item_id == order_item_id,
        )
        .first()
    )
    if item is None:
        raise HTTPException(status_code=404, detail="Order item is not part of this physical return")
    return item


@router.get("/returns/{return_id}/physical")
def customer_physical_return(
    return_id: int,
    customer=Depends(get_current_customer),
    db: Session = Depends(get_db),
):
    ret = (
        db.query(ReturnRequest)
        .filter(ReturnRequest.id == return_id, ReturnRequest.customer_id == customer.id)
        .first()
    )
    if ret is None:
        raise HTTPException(status_code=404, detail="Return request not found")
    case = db.query(ReturnLogisticsCase).filter(ReturnLogisticsCase.return_request_id == ret.id).first()
    if case is None:
        return {
            "return_request_id": ret.id,
            "financial_status": ret.status,
            "physical_status": "not_started",
            "items": [],
        }
    summary = physical_case_summary(db, case)
    return {"financial_status": ret.status, "physical_status": case.status, **summary}


@router.get("/admin/returns/{return_id}/physical")
def admin_physical_return(
    return_id: int,
    admin=Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    require_permission(db, admin, "orders.read")
    ret = db.query(ReturnRequest).filter(ReturnRequest.id == return_id).first()
    if ret is None:
        raise HTTPException(status_code=404, detail="Return request not found")
    case = db.query(ReturnLogisticsCase).filter(ReturnLogisticsCase.return_request_id == ret.id).first()
    if case is None:
        return {
            "return_request_id": ret.id,
            "financial_status": ret.status,
            "physical_status": "not_started",
            "items": [],
        }
    return {"financial_status": ret.status, "physical_status": case.status, **physical_case_summary(db, case)}


def _mutation_context(db: Session, return_id: int, admin):
    require_permission(db, admin, RETURNS_PHYSICAL_WRITE_PERMISSION)
    ret, order = _lock_return_root(db, return_id)
    case = ensure_physical_case(db, ret=ret, order=order)
    return ret, order, case


@router.post("/admin/returns/{return_id}/physical/authorize")
def authorize_physical_return_item(
    return_id: int,
    payload: PhysicalQuantityIn,
    idempotency_key: str = Header(alias="Idempotency-Key"),
    admin=Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    try:
        ret, _order, case = _mutation_context(db, return_id, admin)
        item = _case_item_by_order_item(db, case.id, payload.order_item_id)
        result = authorize_item(
            db,
            case_id=case.id,
            item_id=item.id,
            quantity=payload.quantity,
            idempotency_key=idempotency_key,
            actor_admin_id=admin.id,
            reason=payload.reason,
        )
        log_admin_action(db, admin, "return.physical.authorize", "return_request", ret.id, {
            "case_id": case.id,
            "order_item_id": payload.order_item_id,
            "quantity": payload.quantity,
            "idempotent": result.idempotent,
        })
        db.commit()
        return {"idempotent": result.idempotent, **physical_case_summary(db, case)}
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise


@router.post("/admin/returns/{return_id}/physical/in-transit")
def physical_return_in_transit(
    return_id: int,
    admin=Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    try:
        ret, _order, case = _mutation_context(db, return_id, admin)
        mark_in_transit(db, case_id=case.id)
        log_admin_action(db, admin, "return.physical.in_transit", "return_request", ret.id, {"case_id": case.id})
        db.commit()
        return physical_case_summary(db, case)
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise


@router.post("/admin/returns/{return_id}/physical/receive")
def receive_physical_return_item(
    return_id: int,
    payload: PhysicalQuantityIn,
    idempotency_key: str = Header(alias="Idempotency-Key"),
    admin=Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    try:
        ret, _order, case = _mutation_context(db, return_id, admin)
        item = _case_item_by_order_item(db, case.id, payload.order_item_id)
        result = receive_item(
            db,
            case_id=case.id,
            item_id=item.id,
            quantity=payload.quantity,
            idempotency_key=idempotency_key,
            actor_admin_id=admin.id,
            reason=payload.reason,
        )
        log_admin_action(db, admin, "return.physical.receive", "return_request", ret.id, {
            "case_id": case.id,
            "order_item_id": payload.order_item_id,
            "quantity": payload.quantity,
            "idempotent": result.idempotent,
        })
        db.commit()
        return {"idempotent": result.idempotent, **physical_case_summary(db, case)}
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise


@router.post("/admin/returns/{return_id}/physical/inspect")
def inspect_physical_return_item(
    return_id: int,
    payload: PhysicalInspectionIn,
    idempotency_key: str = Header(alias="Idempotency-Key"),
    admin=Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    try:
        ret, _order, case = _mutation_context(db, return_id, admin)
        item = _case_item_by_order_item(db, case.id, payload.order_item_id)
        result = inspect_item(
            db,
            case_id=case.id,
            item_id=item.id,
            quantity=payload.quantity,
            disposition=payload.disposition,
            idempotency_key=idempotency_key,
            actor_admin_id=admin.id,
            reason=payload.reason,
        )
        provider_command = None
        if case.status == "inspected":
            provider_command = enqueue_moysklad_physical_sales_return(db, case.id)
        log_admin_action(db, admin, "return.physical.inspect", "return_request", ret.id, {
            "case_id": case.id,
            "order_item_id": payload.order_item_id,
            "quantity": payload.quantity,
            "disposition": payload.disposition,
            "idempotent": result.idempotent,
            "moysklad_sales_return_queued": provider_command is not None,
        })
        db.commit()
        return {"idempotent": result.idempotent, **physical_case_summary(db, case)}
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise
