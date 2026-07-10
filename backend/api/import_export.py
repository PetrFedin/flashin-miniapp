from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from ..database import get_db
from ..security import get_current_admin
from ..services.import_export import export_orders_csv, export_products_csv
from ..services.rbac import require_permission

router = APIRouter(prefix="/import-export", tags=["import-export"])


@router.post("/admin/export/products")
def export_products(admin=Depends(get_current_admin), db: Session = Depends(get_db)):
    require_permission(db, admin, "products.read")
    return {"path": export_products_csv(db)}


@router.post("/admin/export/orders")
def export_orders(admin=Depends(get_current_admin), db: Session = Depends(get_db)):
    require_permission(db, admin, "orders.read")
    return {"path": export_orders_csv(db)}
