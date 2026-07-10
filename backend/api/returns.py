from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session, joinedload
from ..database import get_db
from ..models import Customer, Order, ReturnRequest
from ..schemas import ReturnCreate, ReturnOut
from ..security import get_current_customer

router = APIRouter(prefix="/returns", tags=["returns"])


@router.post("", response_model=ReturnOut)
def create_return(payload: ReturnCreate, customer: Customer = Depends(get_current_customer), db: Session = Depends(get_db)):
    order = db.query(Order).filter(Order.id == payload.order_id, Order.customer_id == customer.id).first()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    if order.payment_status != "paid":
        raise HTTPException(status_code=409, detail="Only paid orders can be returned")
    ret = ReturnRequest(order_id=order.id, customer_id=customer.id, reason=payload.reason, status="requested")
    order.status = "refund_requested"
    db.add(ret)
    db.commit()
    db.refresh(ret)
    return ret


@router.get("", response_model=list[ReturnOut])
def my_returns(customer: Customer = Depends(get_current_customer), db: Session = Depends(get_db)):
    return db.query(ReturnRequest).filter(ReturnRequest.customer_id == customer.id).order_by(ReturnRequest.created_at.desc()).all()



from ..models import Payment
from ..schemas import RefundApproveIn
from ..security import get_current_admin
from ..services.inventory import release_variant
from ..services.payments import create_yookassa_refund
from ..services.loyalty import refund_redeemed_points


@router.post("/admin/approve")
async def approve_return(payload: RefundApproveIn, admin=Depends(get_current_admin), db: Session = Depends(get_db)):
    ret = db.query(ReturnRequest).filter(ReturnRequest.id == payload.return_id).first()
    if not ret:
        raise HTTPException(status_code=404, detail="Return request not found")
    order = db.query(Order).options(joinedload(Order.items)).filter(Order.id == ret.order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    payment = db.query(Payment).filter(Payment.order_id == order.id, Payment.status.in_(["succeeded", "waiting_for_capture", "paid"])).first()
    if not payment:
        payment = db.query(Payment).filter(Payment.order_id == order.id).order_by(Payment.created_at.desc()).first()
    if not payment or not payment.provider_payment_id:
        raise HTTPException(status_code=409, detail="No provider payment found for refund")

    amount = payload.amount or order.total_amount
    data = await create_yookassa_refund(payment.provider_payment_id, amount, order.currency, order.id)

    for item in order.items:
        release_variant(db, item.variant_id, 0)  # reserved already committed; keep stock logic manual for returns audit
    ret.status = "approved"
    ret.provider_refund_id = data["refund_id"]
    ret.refund_amount = amount
    order.status = "refunded"
    order.payment_status = "refunded"
    refund_redeemed_points(db, order.customer_id, order.id, order.loyalty_points_redeemed)
    db.commit()
    return {"ok": True, "refund_id": data["refund_id"], "status": data["status"]}
