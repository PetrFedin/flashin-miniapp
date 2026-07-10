from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from ..database import get_db
from ..schemas import ProductOut
from ..security import get_current_admin
from ..services.search import rebuild_search_index, search_products
from ..services.meili import index_products, search_products_meili
from ..services.rbac import require_permission

router = APIRouter(prefix="/search", tags=["search"])


@router.get("/products", response_model=list[ProductOut])
def search(q: str = Query(..., min_length=2), db: Session = Depends(get_db)):
    ids = search_products_meili(q)
    if ids:
        products = db.query(__import__("backend.models", fromlist=["Product"]).Product).filter(__import__("backend.models", fromlist=["Product"]).Product.id.in_(ids)).all()
        order = {pid: idx for idx, pid in enumerate(ids)}
        products.sort(key=lambda p: order.get(p.id, 999))
        return products
    return search_products(db, q)


@router.post("/admin/rebuild")
def rebuild(admin=Depends(get_current_admin), db: Session = Depends(get_db)):
    require_permission(db, admin, "products.write")
    count = rebuild_search_index(db)
    from ..models import Product
    meili_count = index_products(db.query(Product).filter(Product.active == True).all())
    return {"ok": True, "indexed": count, "meilisearch_indexed": meili_count}



@router.post("/admin/configure-meili")
def configure_meili(admin=Depends(get_current_admin), db: Session = Depends(get_db)):
    require_permission(db, admin, "products.write")
    from ..services.meili_settings import configure_products_index
    return configure_products_index()
