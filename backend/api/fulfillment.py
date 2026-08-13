from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import FulfillmentTask, FulfillmentTaskItem, OrderItem, SlaEvent
from ..schemas import FulfillmentTaskOut, FulfillmentUpdateIn, SlaEventOut
from ..security import get_current_admin
from ..services.audit import log_admin_action
from ..services.fulfillment import update_fulfillment_status
from ..services.rbac import require_permission

router = APIRouter(prefix="/fulfillment", tags=["fulfillment"])


@router.get("/tasks", response_model=list[FulfillmentTaskOut])
def list_tasks(admin=Depends(get_current_admin), db: Session = Depends(get_db)):
    require_permission(db, admin, "orders.read")
    return (
        db.query(FulfillmentTask)
        .order_by(FulfillmentTask.created_at.desc(), FulfillmentTask.id.desc())
        .limit(200)
        .all()
    )


@router.patch("/tasks/{task_id}", response_model=FulfillmentTaskOut)
def update_task(
    task_id: int,
    payload: FulfillmentUpdateIn,
    admin=Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    require_permission(db, admin, "fulfillment.write")
    try:
        task = (
            db.query(FulfillmentTask)
            .filter(FulfillmentTask.id == task_id)
            .with_for_update()
            .first()
        )
        if not task:
            raise HTTPException(status_code=404, detail="Fulfillment task not found")
        previous_status = task.status
        try:
            update_fulfillment_status(db, task, payload.status, payload.comment)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if task.assigned_admin_id is None:
            task.assigned_admin_id = admin.id
        log_admin_action(
            db,
            admin,
            "fulfillment.task.update",
            "fulfillment_task",
            task.id,
            {
                "order_id": task.order_id,
                "from_status": previous_status,
                "status": task.status,
                "assigned_admin_id": task.assigned_admin_id,
                "comment": task.comment,
            },
        )
        db.commit()
        db.refresh(task)
        return task
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise


@router.get("/sla", response_model=list[SlaEventOut])
def list_sla(admin=Depends(get_current_admin), db: Session = Depends(get_db)):
    require_permission(db, admin, "orders.read")
    return db.query(SlaEvent).order_by(SlaEvent.due_at.asc()).limit(200).all()


@router.get("/tasks/{task_id}/picklist")
def task_picklist(
    task_id: int,
    admin=Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    require_permission(db, admin, "orders.read")
    task = db.query(FulfillmentTask).filter(FulfillmentTask.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Fulfillment task not found")
    items = (
        db.query(FulfillmentTaskItem, OrderItem)
        .join(OrderItem, FulfillmentTaskItem.order_item_id == OrderItem.id)
        .filter(FulfillmentTaskItem.task_id == task.id)
        .order_by(FulfillmentTaskItem.id.asc())
        .all()
    )
    return {
        "task_id": task.id,
        "order_id": task.order_id,
        "status": task.status,
        "items": [
            {
                "task_item_id": task_item.id,
                "order_item_id": order_item.id,
                "title": order_item.title,
                "size": order_item.size,
                "quantity": order_item.quantity,
                "picked_qty": task_item.picked_qty,
                "status": task_item.status,
                "issue": task_item.issue,
            }
            for task_item, order_item in items
        ],
    }


@router.patch("/task-items/{task_item_id}")
def update_task_item(
    task_item_id: int,
    picked_qty: int = Query(default=0, ge=0),
    status: str = Query(default="picked", max_length=64),
    issue: str = Query(default="", max_length=2000),
    admin=Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    require_permission(db, admin, "fulfillment.write")
    normalized_status = status.strip().lower()
    if normalized_status not in {"to_pick", "picked", "issue"}:
        raise HTTPException(status_code=400, detail="Unsupported picklist item status")
    if normalized_status == "issue" and len(issue.strip()) < 5:
        raise HTTPException(status_code=400, detail="Picklist issue requires a meaningful comment")

    try:
        item = (
            db.query(FulfillmentTaskItem)
            .filter(FulfillmentTaskItem.id == task_item_id)
            .with_for_update()
            .first()
        )
        if not item:
            raise HTTPException(status_code=404, detail="Fulfillment task item not found")
        order_item = (
            db.query(OrderItem)
            .filter(OrderItem.id == item.order_item_id)
            .with_for_update()
            .first()
        )
        if not order_item:
            raise HTTPException(status_code=409, detail="Picklist item is linked to a missing order item")
        if picked_qty > order_item.quantity:
            raise HTTPException(status_code=409, detail="Picked quantity exceeds ordered quantity")
        if normalized_status == "picked" and picked_qty != order_item.quantity:
            raise HTTPException(status_code=409, detail="Picked status requires the full ordered quantity")

        previous = {
            "status": item.status,
            "picked_qty": item.picked_qty,
            "issue": item.issue,
        }
        item.picked_qty = picked_qty
        item.status = normalized_status
        item.issue = issue.strip()[:2000]
        log_admin_action(
            db,
            admin,
            "fulfillment.task_item.update",
            "fulfillment_task_item",
            item.id,
            {
                "task_id": item.task_id,
                "order_item_id": item.order_item_id,
                "previous": previous,
                "status": item.status,
                "picked_qty": item.picked_qty,
                "issue": item.issue,
            },
        )
        db.commit()
        return {
            "ok": True,
            "task_item_id": item.id,
            "status": item.status,
            "picked_qty": item.picked_qty,
            "ordered_qty": order_item.quantity,
            "issue": item.issue,
        }
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise
