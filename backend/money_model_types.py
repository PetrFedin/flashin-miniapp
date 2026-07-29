from sqlalchemy import Numeric

from .models import (
    Cart,
    CrmProfile,
    DeliveryShipment,
    DeliveryZone,
    LoyaltyRedemptionHold,
    LoyaltyTransaction,
    Order,
    OrderItem,
    Payment,
    PaymentReconciliation,
    Product,
    PromoCode,
    ReferralCode,
    ReturnRequest,
)

_MONEY_TYPE = Numeric(precision=20, scale=2, asdecimal=False)
_POINT_TYPE = Numeric(precision=20, scale=4, asdecimal=False)
_RATE_TYPE = Numeric(precision=20, scale=4, asdecimal=False)

_MONEY_COLUMNS = (
    Product.__table__.c.price,
    Product.__table__.c.old_price,
    PromoCode.__table__.c.min_amount,
    Order.__table__.c.total_amount,
    Order.__table__.c.delivery_price,
    Order.__table__.c.discount_amount,
    Order.__table__.c.loyalty_discount_amount,
    OrderItem.__table__.c.price,
    Payment.__table__.c.amount,
    ReturnRequest.__table__.c.refund_amount,
    DeliveryZone.__table__.c.price,
    CrmProfile.__table__.c.total_spent,
    CrmProfile.__table__.c.average_order_value,
    PaymentReconciliation.__table__.c.amount_local,
    PaymentReconciliation.__table__.c.amount_provider,
    DeliveryShipment.__table__.c.price,
)

_POINT_COLUMNS = (
    Cart.__table__.c.loyalty_points_to_redeem,
    Order.__table__.c.loyalty_points_redeemed,
    CrmProfile.__table__.c.loyalty_points,
    LoyaltyTransaction.__table__.c.points_delta,
    ReferralCode.__table__.c.reward_points,
    LoyaltyRedemptionHold.__table__.c.points,
)

_RATE_COLUMNS = (
    PromoCode.__table__.c.discount_value,
)


def apply_money_model_types() -> None:
    """Align SQLAlchemy metadata with PostgreSQL fixed-precision storage.

    ``asdecimal=False`` preserves the existing Python/JSON contract while the
    database and bind layer enforce deterministic scale instead of binary
    floating-point storage.
    """
    for column in _MONEY_COLUMNS:
        column.type = _MONEY_TYPE.copy()
    for column in _POINT_COLUMNS:
        column.type = _POINT_TYPE.copy()
    for column in _RATE_COLUMNS:
        column.type = _RATE_TYPE.copy()
