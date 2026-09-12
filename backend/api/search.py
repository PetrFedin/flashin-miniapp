from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session, selectinload

from ..database import get_db
from ..models import Product
from ..schemas import ProductOut
from ..security import get_current_admin
from ..services.meili import index_product_documents, product_document, search_products_meili
from ..services.rbac import require_permission
from ..services.search import rebuild_search_index, search_products

router = APIRouter(prefix="/search", tags=["search"])


def _end_request_transaction_before_provider_io(db: Session) -> None:
    """Release request-owned DB state before an outbound provider call.

    At the search-admin boundary all durable local writes have already been
    committed. Any remaining transaction contains authorization or snapshot
    reads only, so rollback safely releases the connection without discarding
    committed search-index work.
    """

    if db.in_transaction():
        db.rollback()
    if db.in_transaction():
        raise RuntimeError("database transaction must be closed before Meilisearch I/O")


@router.get("/products", response_model=list[ProductOut])
def search(q: str = Query(..., min_length=2), db: Session = Depends(get_db)):
    ids = search_products_meili(q)
    if ids:
        products = db.query(Product).filter(Product.id.in_(ids)).all()
        order = {pid: idx for idx, pid in enumerate(ids)}
        products.sort(key=lambda p: order.get(p.id, 999))
        return products
    return search_products(db, q)


@router.post("/admin/rebuild")
def rebuild(admin=Depends(get_current_admin), db: Session = Depends(get_db)):
    require_permission(db, admin, "products.write")
    count = rebuild_search_index(db)
    products = (
        db.query(Product)
        .options(selectinload(Product.images))
        .filter(Product.active.is_(True))
        .all()
    )
    documents = [product_document(product) for product in products]
    _end_request_transaction_before_provider_io(db)
    meili_count = index_product_documents(documents)
    return {"ok": True, "indexed": count, "meilisearch_indexed": meili_count}


@router.post("/admin/configure-meili")
def configure_meili(admin=Depends(get_current_admin), db: Session = Depends(get_db)):
    require_permission(db, admin, "products.write")
    _end_request_transaction_before_provider_io(db)
    from ..services.meili_settings import configure_products_index

    return configure_products_index()
