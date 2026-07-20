from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from ..database import get_db
from ..models import Cart, Customer, Order
from ..security import get_current_admin
from ..services.rbac import require_permission

router = APIRouter(prefix="/business-analytics", tags=["business-analytics"])


@router.get("/summary")
def summary(admin=Depends(get_current_admin), db: Session = Depends(get_db)):
    require_permission(db, admin, "analytics.read")
    paid_orders = db.query(Order).filter(Order.payment_status == "paid").all()
    orders_count = len(paid_orders)
    gmv = sum(o.total_amount for o in paid_orders)
    aov = round(gmv / orders_count, 2) if orders_count else 0
    customers = db.query(Customer).count()
    active_carts = db.query(Cart).filter(Cart.status == "active").count()
    return {
        "gmv": gmv,
        "orders_count": orders_count,
        "aov": aov,
        "customers": customers,
        "active_carts": active_carts,
    }
