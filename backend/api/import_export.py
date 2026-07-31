from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from ..database import get_db
from ..security import get_current_admin
from ..services.audit import log_admin_action
from ..services.import_export import (
    CSV_MEDIA_TYPE,
    export_filename,
    stream_orders_csv,
    stream_products_csv,
)
from ..services.rbac import require_permission

router = APIRouter(prefix="/import-export", tags=["import-export"])


def _download_response(content, filename: str) -> StreamingResponse:
    return StreamingResponse(
        content,
        media_type=CSV_MEDIA_TYPE,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store, max-age=0",
            "Pragma": "no-cache",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.post("/admin/export/products")
def export_products(admin=Depends(get_current_admin), db: Session = Depends(get_db)):
    require_permission(db, admin, "products.read")
    filename = export_filename("products")
    log_admin_action(
        db,
        admin,
        "catalog.export_requested",
        "product",
        payload={"format": "csv", "filename": filename},
    )
    db.commit()
    return _download_response(stream_products_csv(db), filename)


@router.post("/admin/export/orders")
def export_orders(admin=Depends(get_current_admin), db: Session = Depends(get_db)):
    require_permission(db, admin, "orders.read")
    filename = export_filename("orders")
    log_admin_action(
        db,
        admin,
        "orders.export_requested",
        "order",
        payload={"format": "csv", "filename": filename},
    )
    db.commit()
    return _download_response(stream_orders_csv(db), filename)
