from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
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
from ..services.product_csv_import import (
    MAX_PRODUCT_CSV_BYTES,
    import_products_csv,
)
from ..services.rbac import require_permission

router = APIRouter(prefix="/import-export", tags=["import-export"])
_ALLOWED_IMPORT_CONTENT_TYPES = frozenset(
    {
        "",
        "application/csv",
        "application/octet-stream",
        "application/vnd.ms-excel",
        "text/csv",
    }
)


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


@router.post("/admin/products/import-csv")
async def import_products(
    file: UploadFile = File(...),
    admin=Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    require_permission(db, admin, "products.write")
    filename = str(file.filename or "").strip()
    content_type = str(file.content_type or "").split(";", 1)[0].strip().lower()
    if not filename.lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="Product import file must have a .csv extension")
    if content_type not in _ALLOWED_IMPORT_CONTENT_TYPES:
        raise HTTPException(status_code=415, detail="Product import file must be CSV")

    try:
        content = await file.read(MAX_PRODUCT_CSV_BYTES + 1)
        result = import_products_csv(db, content)
        payload = result.as_dict()
        log_admin_action(
            db,
            admin,
            "catalog.csv_imported",
            "product",
            payload={"filename": filename, **payload},
        )
        db.commit()
        return {"ok": True, **payload}
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise
    finally:
        await file.close()
