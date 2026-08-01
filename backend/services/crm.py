from sqlalchemy.orm import Session

from ..config import get_settings
from ..database import utcnow_naive
from ..models import CrmProfile, Customer, Order


def recompute_customer_profile(db: Session, customer_id: int) -> CrmProfile:
    settings = get_settings()
    orders = (
        db.query(Order)
        .filter(
            Order.customer_id == customer_id,
            Order.payment_status.in_(["paid", "refunded"]),
        )
        .all()
    )
    paid_orders = [order for order in orders if order.payment_status == "paid"]
    total = sum(order.total_amount for order in paid_orders)
    count = len(paid_orders)
    average_order_value = round(total / count, 2) if count else 0
    last_order_at = max((order.created_at for order in paid_orders), default=None)

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
    profile.average_order_value = average_order_value
    profile.last_order_at = last_order_at
    profile.loyalty_points = round(total * settings.loyalty_points_per_ruble, 2)
    profile.vip = segment == "vip"
    profile.updated_at = utcnow_naive()
    return profile


def recompute_all_profiles(db: Session) -> int:
    customers = db.query(Customer).all()
    for customer in customers:
        recompute_customer_profile(db, customer.id)
    db.commit()
    return len(customers)
