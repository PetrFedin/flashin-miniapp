from sqlalchemy.orm import Session
from ..models import Product, ProductRecommendation


def rebuild_basic_recommendations(db: Session) -> int:
    db.query(ProductRecommendation).delete()
    products = db.query(Product).filter(Product.active == True).all()
    count = 0
    for product in products:
        candidates = [p for p in products if p.id != product.id and p.category == product.category][:4]
        if not candidates:
            candidates = [p for p in products if p.id != product.id][:4]
        for idx, rec in enumerate(candidates):
            db.add(ProductRecommendation(product_id=product.id, recommended_product_id=rec.id, score=1.0 - idx * 0.1, source="category"))
            count += 1
    db.commit()
    return count
