from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from ..database import get_db
from ..models import FulfillmentTask, SlaEvent
from ..schemas import FulfillmentTaskOut, FulfillmentUpdateIn, SlaEventOut
from ..security import get_current_admin
from ..services.fulfillment import update_fulfillment_status
from ..services.rbac import require_permission

router = APIRouter(prefix="/fulfillment", tags=["fulfillment"])


@router.get("/tasks", response_model=list[FulfillmentTaskOut])
def list_tasks(admin=Depends(get_current_admin), db: Session = Depends(get_db)):
    require_permission(db, admin, "orders.read")
    return db.query(FulfillmentTask).order_by(FulfillmentTask.created_at.desc()).limit(200).all()


@router.patch("/tasks/{task_id}", response_model=FulfillmentTaskOut)
def update_task(task_id: int, payload: FulfillmentUpdateIn, admin=Depends(get_current_admin), db: Session = Depends(get_db)):
    require_permission(db, admin, "orders.write")
    task = db.query(FulfillmentTask).filter(FulfillmentTask.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Fulfillment task not found")
    update_fulfillment_status(task, payload.status, payload.comment)
    db.commit()
    return task


@router.get("/sla", response_model=list[SlaEventOut])
def list_sla(admin=Depends(get_current_admin), db: Session = Depends(get_db)):
    require_permission(db, admin, "orders.read")
    return db.query(SlaEvent).order_by(SlaEvent.due_at.asc()).limit(200).all()



@router.get("/tasks/{task_id}/picklist")
def task_picklist(task_id: int, admin=Depends(get_current_admin), db: Session = Depends(get_db)):
    require_permission(db, admin, "orders.read")
    from ..models import FulfillmentTaskItem, OrderItem
    task = db.query(FulfillmentTask).filter(FulfillmentTask.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Fulfillment task not found")
    items = (
        db.query(FulfillmentTaskItem, OrderItem)
        .join(OrderItem, FulfillmentTaskItem.order_item_id == OrderItem.id)
        .filter(FulfillmentTaskItem.task_id == task.id)
        .all()
    )
    return {
        "task_id": task.id,
        "order_id": task.order_id,
        "status": task.status,
        "items": [
            {
                "task_item_id": ti.id,
                "order_item_id": oi.id,
                "title": oi.title,
                "size": oi.size,
                "quantity": oi.quantity,
                "picked_qty": ti.picked_qty,
                "status": ti.status,
                "issue": ti.issue,
            }
            for ti, oi in items
        ],
    }


@router.patch("/task-items/{task_item_id}")
def update_task_item(task_item_id: int, picked_qty: int = 0, status: str = "picked", issue: str = "", admin=Depends(get_current_admin), db: Session = Depends(get_db)):
    require_permission(db, admin, "orders.write")
    from ..models import FulfillmentTaskItem
    item = db.query(FulfillmentTaskItem).filter(FulfillmentTaskItem.id == task_item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Fulfillment task item not found")
    item.picked_qty = picked_qty
    item.status = status
    item.issue = issue
    db.commit()
    return {"ok": True}
