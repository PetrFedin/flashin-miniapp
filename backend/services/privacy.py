import json
from datetime import datetime
from sqlalchemy.orm import Session
from ..models import Cart, ConsentRecord, Customer, Order, PrivacyRequest, WishlistItem


def build_customer_export(db: Session, customer: Customer) -> dict:
    orders = db.query(Order).filter(Order.customer_id == customer.id).all()
    carts = db.query(Cart).filter(Cart.customer_id == customer.id).all()
    wishlist = db.query(WishlistItem).filter(WishlistItem.customer_id == customer.id).all()
    consents = db.query(ConsentRecord).filter(ConsentRecord.customer_id == customer.id).all()
    return {
        "customer": {
            "id": customer.id,
            "telegram_id": customer.telegram_id,
            "username": customer.username,
            "first_name": customer.first_name,
            "phone": customer.phone,
            "email": customer.email,
        },
        "orders": [{"id": o.id, "status": o.status, "payment_status": o.payment_status, "total_amount": o.total_amount} for o in orders],
        "carts": [{"id": c.id, "status": c.status} for c in carts],
        "wishlist": [{"product_id": w.product_id} for w in wishlist],
        "consents": [{"type": c.consent_type, "granted": c.granted, "created_at": c.created_at.isoformat()} for c in consents],
    }


def mark_privacy_processed(req: PrivacyRequest, result_url: str = "") -> None:
    req.status = "processed"
    req.result_url = result_url
    req.processed_at = datetime.utcnow()
