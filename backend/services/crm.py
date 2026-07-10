from datetime import datetime
from sqlalchemy.orm import Session
from ..config import get_settings
from ..models import CrmProfile, Customer, Order


def recompute_customer_profile(db: Session, customer_id: int) -> CrmProfile:
    settings = get_settings()
    orders = db.query(Order).filter(Order.customer_id == customer_id, Order.payment_status.in_(["paid", "refunded"])).all()
    paid_orders = [o for o in orders if o.payment_status == "paid"]
    total = sum(o.total_amount for o in paid_orders)
    count = len(paid_orders)
    aov = round(total / count, 2) if count else 0
    last = max((o.created_at for o in paid_orders), default=None)

    if count == 0:
        segment = "new"
    elif total >= 100000 or count >= 5:
        segment = "vip"
    elif count >= 2:
        segment = "repeat"
    else:
        segment = "first_purchase"

    profile = db.query(CrmProfile).filter(CrmProfile.customer_id == customer_id).first()
    if not profile:
        profile = CrmProfile(customer_id=customer_id)
        db.add(profile)
    profile.segment = segment
    profile.orders_count = count
    profile.total_spent = total
    profile.average_order_value = aov
    profile.last_order_at = last
    profile.loyalty_points = round(total * settings.loyalty_points_per_ruble, 2)
    profile.vip = segment == "vip"
    profile.updated_at = datetime.utcnow()
    return profile


def recompute_all_profiles(db: Session) -> int:
    customers = db.query(Customer).all()
    for c in customers:
        recompute_customer_profile(db, c.id)
    db.commit()
    return len(customers)
