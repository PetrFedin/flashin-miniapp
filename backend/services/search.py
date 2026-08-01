from sqlalchemy.orm import Session

from ..database import utcnow_naive
from ..models import Product, ProductSearchIndex


def rebuild_search_index(db: Session) -> int:
    products = db.query(Product).all()
    for product in products:
        search_text = " ".join(
            [
                product.title or "",
                product.sku or "",
                product.brand or "",
                product.category or "",
                product.description or "",
            ]
        ).lower()
        row = (
            db.query(ProductSearchIndex)
            .filter(ProductSearchIndex.product_id == product.id)
            .first()
        )
        if not row:
            row = ProductSearchIndex(product_id=product.id, search_text=search_text)
            db.add(row)
        else:
            row.search_text = search_text
            row.updated_at = utcnow_naive()
    db.commit()
    return len(products)


def search_products(db: Session, query: str, limit: int = 20):
    tokens = [token.lower() for token in query.split() if token.strip()]
    if not tokens:
        return []
    rows = db.query(ProductSearchIndex).all()
    scored = []
    for row in rows:
        score = sum(1 for token in tokens if token in row.search_text)
        if score:
            scored.append((score, row.product_id))
    scored.sort(reverse=True)
    product_ids = [product_id for _, product_id in scored[:limit]]
    if not product_ids:
        return []
    products = (
        db.query(Product)
        .filter(Product.id.in_(product_ids), Product.active.is_(True))
        .all()
    )
    order = {product_id: index for index, product_id in enumerate(product_ids)}
    products.sort(key=lambda product: order.get(product.id, 999))
    return products
