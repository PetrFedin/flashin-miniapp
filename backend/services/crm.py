from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from sqlalchemy.orm import Session

from ..database import utcnow_naive
from ..models import CrmProfile, Customer, Order

_MONEY_STEP = Decimal("0.01")


def _decimal_value(value, field: str) -> Decimal:
    try:
        amount = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"Invalid {field}") from exc
    if not amount.is_finite():
        raise ValueError(f"Invalid {field}")
    return amount


def recompute_customer_profile(db: Session, customer_id: int) -> CrmProfile:
    """Rebuild descriptive CRM metrics without mutating loyalty authority.

    Loyalty balances are maintained by the loyalty ledger/services. A CRM
    rebuild must not derive or overwrite ``CrmProfile.loyalty_points`` from
    paid-order turnover because redemption, refund restoration, referral
    rewards/reversals, expiry and manual ledger adjustments are independent
    balance movements.
    """

    orders = (
        db.query(Order)
        .filter(
            Order.customer_id == customer_id,
            Order.payment_status.in_(["paid", "refunded"]),
        )
        .all()
    )
    paid_orders = [order for order in orders if order.payment_status == "paid"]
    total = sum(
        (_decimal_value(order.total_amount, "order total") for order in paid_orders),
        Decimal("0.00"),
    ).quantize(_MONEY_STEP, rounding=ROUND_HALF_UP)
    count = len(paid_orders)
    average_order_value = (
        (total / Decimal(count)).quantize(_MONEY_STEP, rounding=ROUND_HALF_UP)
        if count
        else Decimal("0.00")
    )
    last_order_at = max((order.created_at for order in paid_orders), default=None)

    if count == 0:
        segment = "new"
    elif total >= Decimal("100000.00") or count >= 5:
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
    profile.vip = segment == "vip"
    profile.updated_at = utcnow_naive()
    return profile


def recompute_all_profiles(db: Session) -> int:
    """Stage descriptive CRM recomputation; the caller owns the transaction."""

    customers = db.query(Customer).all()
    for customer in customers:
        recompute_customer_profile(db, customer.id)
    return len(customers)
