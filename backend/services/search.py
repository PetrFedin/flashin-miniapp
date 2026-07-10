from datetime import datetime
from sqlalchemy.orm import Session
from ..models import Product, ProductSearchIndex


def rebuild_search_index(db: Session) -> int:
    products = db.query(Product).all()
    for p in products:
        text = " ".join([p.title or "", p.sku or "", p.brand or "", p.category or "", p.description or ""]).lower()
        row = db.query(ProductSearchIndex).filter(ProductSearchIndex.product_id == p.id).first()
        if not row:
            row = ProductSearchIndex(product_id=p.id, search_text=text)
            db.add(row)
        else:
            row.search_text = text
            row.updated_at = datetime.utcnow()
    db.commit()
    return len(products)


def search_products(db: Session, query: str, limit: int = 20):
    tokens = [t.lower() for t in query.split() if t.strip()]
    if not tokens:
        return []
    rows = db.query(ProductSearchIndex).all()
    scored = []
    for row in rows:
        score = sum(1 for token in tokens if token in row.search_text)
        if score:
            scored.append((score, row.product_id))
    scored.sort(reverse=True)
    ids = [pid for _, pid in scored[:limit]]
    if not ids:
        return []
    products = db.query(Product).filter(Product.id.in_(ids), Product.active == True).all()
    order = {pid: i for i, pid in enumerate(ids)}
    products.sort(key=lambda p: order.get(p.id, 999))
    return products
