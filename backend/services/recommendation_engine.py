from sqlalchemy.orm import Session
from ..models import AnalyticsEvent, Order, Product, ProductRecommendation, WishlistItem


def rebuild_recommendations_v2(db: Session) -> int:
    db.query(ProductRecommendation).delete()
    products = db.query(Product).filter(Product.active == True).all()
    count = 0
    for p in products:
        candidates = [x for x in products if x.id != p.id and x.category == p.category]
        candidates += [x for x in products if x.id != p.id and x.brand == p.brand and x not in candidates]
        for idx, rec in enumerate(candidates[:8]):
            db.add(ProductRecommendation(product_id=p.id, recommended_product_id=rec.id, score=1.0 - idx * 0.05, source="v2_category_brand"))
            count += 1
    db.commit()
    return count


def personal_recommendations(db: Session, customer_id: int, limit: int = 12):
    wishlist_product_ids = [w.product_id for w in db.query(WishlistItem).filter(WishlistItem.customer_id == customer_id).all()]
    category_counts = {}
    if wishlist_product_ids:
        for p in db.query(Product).filter(Product.id.in_(wishlist_product_ids)).all():
            category_counts[p.category] = category_counts.get(p.category, 0) + 1
    top_categories = sorted(category_counts, key=category_counts.get, reverse=True)
    query = db.query(Product).filter(Product.active == True)
    if top_categories:
        query = query.filter(Product.category.in_(top_categories))
    return query.limit(limit).all()
