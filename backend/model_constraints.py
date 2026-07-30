"""Database constraints that must exist in both create_all and Alembic metadata.

Production applies the same rules through Alembic revisions. Keeping them in
metadata prevents local/test databases created with ``Base.metadata.create_all``
from being weaker than PostgreSQL production.
"""

from fastapi import HTTPException
from sqlalchemy import CheckConstraint, Index, UniqueConstraint, event, text

from .models import (
    AdminPasswordReset,
    AdminRolePermission,
    AdminSession,
    Cart,
    CartItem,
    CrmProfile,
    DeliveryShipment,
    FulfillmentTask,
    LoyaltyRedemptionHold,
    LoyaltyTransaction,
    Order,
    OrderItem,
    Payment,
    PaymentEvent,
    ProductVariant,
    PromoCode,
    ReferralCode,
    ReturnRequest,
    WebhookDestination,
    WebhookOutbox,
)
from .money_model_types import apply_money_model_types
from .order_statuses import (
    DELIVERY_STATUS_SQL,
    ORDER_DELIVERY_COHERENCE_SQL,
    ORDER_PAYMENT_COHERENCE_SQL,
    ORDER_STATUS_SQL,
    PAYMENT_STATUS_SQL,
)
from .return_statuses import (
    AMOUNT_REQUIRED_RETURN_STATUS_SQL,
    OPEN_RETURN_STATUS_SQL,
    PROVIDER_LINKED_RETURN_STATUS_SQL,
    VALID_RETURN_STATUS_SQL,
)
from .services.pricing import validate_promo_definition


def _constraint_names(table) -> set[str]:
    return {constraint.name for constraint in table.constraints if constraint.name}


def _index_names(table) -> set[str]:
    return {index.name for index in table.indexes if index.name}


def _check(table, name: str, expression: str) -> None:
    if name not in _constraint_names(table):
        table.append_constraint(CheckConstraint(expression, name=name))


def _unique(table, name: str, *columns: str) -> None:
    if name not in _constraint_names(table):
        table.append_constraint(UniqueConstraint(*columns, name=name))


def _index(table, name: str, columns: list) -> None:
    if name not in _index_names(table):
        Index(name, *columns)


def _partial_unique_index(table, name: str, columns: list, where: str) -> None:
    if name in _index_names(table):
        return
    predicate = text(where)
    Index(
        name,
        *columns,
        unique=True,
        postgresql_where=predicate,
        sqlite_where=predicate,
    )


def _validate_promo_before_write(_mapper, _connection, target: PromoCode) -> None:
    code = str(target.code or "").strip().upper()
    if not code:
        raise HTTPException(status_code=400, detail="Promo code is required")
    if len(code) > 64:
        raise HTTPException(status_code=400, detail="Promo code is too long")

    max_uses = 0 if target.max_uses is None else target.max_uses
    used_count = 0 if target.used_count is None else target.used_count
    promo_type, discount_value, minimum, max_uses, used_count = validate_promo_definition(
        target.discount_type,
        target.discount_value,
        0 if target.min_amount is None else target.min_amount,
        max_uses,
        used_count,
        status_code=400,
    )
    target.code = code
    target.discount_type = promo_type
    target.discount_value = float(discount_value)
    target.min_amount = float(minimum)
    target.max_uses = max_uses
    target.used_count = used_count


def _register_promo_validation() -> None:
    for event_name in ("before_insert", "before_update"):
        if not event.contains(PromoCode, event_name, _validate_promo_before_write):
            event.listen(PromoCode, event_name, _validate_promo_before_write)


def apply_model_constraints() -> None:
    apply_money_model_types()
    _register_promo_validation()

    _check(ProductVariant.__table__, "ck_product_variants_stock_nonnegative", "stock_qty >= 0")
    _check(ProductVariant.__table__, "ck_product_variants_reserved_nonnegative", "reserved_qty >= 0")
    _check(ProductVariant.__table__, "ck_product_variants_reserved_within_stock", "reserved_qty <= stock_qty")

    _check(CartItem.__table__, "ck_cart_items_quantity_positive", "quantity > 0")
    _check(CartItem.__table__, "ck_cart_items_quantity_limit", "quantity <= 10")

    _check(OrderItem.__table__, "ck_order_items_quantity_positive", "quantity > 0")
    _check(OrderItem.__table__, "ck_order_items_price_nonnegative", "price >= 0")

    _check(Order.__table__, "ck_orders_total_nonnegative", "total_amount >= 0")
    _check(Order.__table__, "ck_orders_delivery_nonnegative", "delivery_price >= 0")
    _check(Order.__table__, "ck_orders_discount_nonnegative", "discount_amount >= 0")
    _check(Order.__table__, "ck_orders_loyalty_points_nonnegative", "loyalty_points_redeemed >= 0")
    _check(Order.__table__, "ck_orders_loyalty_discount_nonnegative", "loyalty_discount_amount >= 0")
    _check(Order.__table__, "ck_orders_status_valid", f"status IN ({ORDER_STATUS_SQL})")
    _check(
        Order.__table__,
        "ck_orders_payment_status_valid",
        f"payment_status IN ({PAYMENT_STATUS_SQL})",
    )
    _check(
        Order.__table__,
        "ck_orders_delivery_status_valid",
        f"delivery_status IN ({DELIVERY_STATUS_SQL})",
    )
    _check(
        Order.__table__,
        "ck_orders_payment_state_coherent",
        ORDER_PAYMENT_COHERENCE_SQL,
    )
    _check(
        Order.__table__,
        "ck_orders_delivery_state_coherent",
        ORDER_DELIVERY_COHERENCE_SQL,
    )

    _check(PromoCode.__table__, "ck_promo_codes_code_nonempty", "length(trim(code)) > 0")
    _check(PromoCode.__table__, "ck_promo_codes_code_normalized", "code = upper(trim(code))")
    _check(
        PromoCode.__table__,
        "ck_promo_codes_discount_type_valid",
        "discount_type IN ('percent', 'fixed')",
    )
    _check(PromoCode.__table__, "ck_promo_codes_discount_nonnegative", "discount_value >= 0")
    _check(PromoCode.__table__, "ck_promo_codes_discount_positive", "discount_value > 0")
    _check(
        PromoCode.__table__,
        "ck_promo_codes_percent_within_100",
        "discount_type <> 'percent' OR discount_value <= 100",
    )
    _check(PromoCode.__table__, "ck_promo_codes_min_amount_nonnegative", "min_amount >= 0")
    _check(PromoCode.__table__, "ck_promo_codes_max_uses_nonnegative", "max_uses >= 0")
    _check(PromoCode.__table__, "ck_promo_codes_used_count_nonnegative", "used_count >= 0")
    _check(
        PromoCode.__table__,
        "ck_promo_codes_usage_within_limit",
        "max_uses = 0 OR used_count <= max_uses",
    )

    _check(Payment.__table__, "ck_payments_amount_positive", "amount > 0")
    _check(ReturnRequest.__table__, "ck_return_requests_refund_nonnegative", "refund_amount >= 0")
    _check(
        ReturnRequest.__table__,
        "ck_return_requests_reason_length",
        "length(trim(reason)) BETWEEN 5 AND 2000",
    )
    _check(
        ReturnRequest.__table__,
        "ck_return_requests_reason_normalized",
        "reason = trim(reason)",
    )
    _check(
        ReturnRequest.__table__,
        "ck_return_requests_status_valid",
        f"status IN ({VALID_RETURN_STATUS_SQL})",
    )
    _check(
        ReturnRequest.__table__,
        "ck_return_requests_amount_required",
        f"status NOT IN ({AMOUNT_REQUIRED_RETURN_STATUS_SQL}) OR refund_amount > 0",
    )
    _check(
        ReturnRequest.__table__,
        "ck_return_requests_provider_id_normalized",
        "provider_refund_id = trim(provider_refund_id)",
    )
    _check(
        ReturnRequest.__table__,
        "ck_return_requests_provider_id_required",
        (
            f"status NOT IN ({PROVIDER_LINKED_RETURN_STATUS_SQL}) "
            "OR length(provider_refund_id) > 0"
        ),
    )
    _check(CrmProfile.__table__, "ck_crm_profiles_loyalty_nonnegative", "loyalty_points >= 0")
    _check(LoyaltyRedemptionHold.__table__, "ck_loyalty_holds_points_positive", "points > 0")
    _check(WebhookOutbox.__table__, "ck_webhook_outbox_attempts_nonnegative", "attempts >= 0")
    _check(WebhookDestination.__table__, "ck_webhook_destinations_url_nonempty", "length(trim(url)) > 0")
    _check(
        WebhookDestination.__table__,
        "ck_webhook_destinations_event_type_nonempty",
        "length(trim(event_type)) > 0",
    )
    _check(DeliveryShipment.__table__, "ck_delivery_shipments_price_nonnegative", "price >= 0")
    _check(
        DeliveryShipment.__table__,
        "ck_delivery_shipments_provider_nonempty",
        "length(trim(provider_code)) > 0",
    )
    _check(
        DeliveryShipment.__table__,
        "ck_delivery_shipments_status_valid",
        "status IN ('created', 'shipped', 'delivery_failed', 'delivered', 'returned', 'cancelled')",
    )

    _unique(
        AdminRolePermission.__table__,
        "uq_admin_role_permissions_role_permission",
        "role",
        "permission",
    )
    _unique(FulfillmentTask.__table__, "uq_fulfillment_tasks_order_id", "order_id")
    _unique(DeliveryShipment.__table__, "uq_delivery_shipments_order_id", "order_id")
    _unique(AdminSession.__table__, "uq_admin_sessions_token_hash", "session_token_hash")
    _unique(AdminPasswordReset.__table__, "uq_admin_password_resets_token_hash", "token_hash")
    _unique(
        WebhookDestination.__table__,
        "uq_webhook_destinations_url_event_type",
        "url",
        "event_type",
    )

    _index(
        WebhookOutbox.__table__,
        "ix_webhook_outbox_due",
        [
            WebhookOutbox.__table__.c.status,
            WebhookOutbox.__table__.c.next_attempt_at,
            WebhookOutbox.__table__.c.id,
        ],
    )

    _partial_unique_index(
        Cart.__table__,
        "uq_carts_one_active_per_customer",
        [Cart.__table__.c.customer_id],
        "status = 'active'",
    )
    _partial_unique_index(
        Payment.__table__,
        "uq_payments_provider_payment_id",
        [Payment.__table__.c.provider, Payment.__table__.c.provider_payment_id],
        "provider_payment_id <> ''",
    )
    _partial_unique_index(
        PaymentEvent.__table__,
        "uq_payment_events_provider_event",
        [
            PaymentEvent.__table__.c.provider,
            PaymentEvent.__table__.c.provider_payment_id,
            PaymentEvent.__table__.c.event_type,
        ],
        "provider_payment_id <> '' AND event_type <> ''",
    )
    _partial_unique_index(
        ReturnRequest.__table__,
        "uq_return_requests_provider_refund_id",
        [ReturnRequest.__table__.c.provider_refund_id],
        "provider_refund_id <> ''",
    )
    _partial_unique_index(
        ReturnRequest.__table__,
        "uq_return_requests_one_open_per_order",
        [ReturnRequest.__table__.c.order_id],
        f"status IN ({OPEN_RETURN_STATUS_SQL})",
    )
    _partial_unique_index(
        LoyaltyTransaction.__table__,
        "uq_loyalty_transactions_order_reason",
        [
            LoyaltyTransaction.__table__.c.customer_id,
            LoyaltyTransaction.__table__.c.order_id,
            LoyaltyTransaction.__table__.c.reason,
        ],
        "order_id IS NOT NULL AND reason IN ("
        "'order_paid', 'loyalty_redeemed', 'referral_reward', 'loyalty_refund', "
        "'order_refund_reversal', 'referral_refund_reversal'"
        ")",
    )
    _partial_unique_index(
        LoyaltyRedemptionHold.__table__,
        "uq_loyalty_holds_reserved_cart",
        [
            LoyaltyRedemptionHold.__table__.c.customer_id,
            LoyaltyRedemptionHold.__table__.c.cart_id,
        ],
        "cart_id IS NOT NULL AND status = 'reserved'",
    )
    _partial_unique_index(
        LoyaltyRedemptionHold.__table__,
        "uq_loyalty_holds_order",
        [
            LoyaltyRedemptionHold.__table__.c.customer_id,
            LoyaltyRedemptionHold.__table__.c.order_id,
        ],
        "order_id IS NOT NULL AND status IN ('committed', 'refunded')",
    )
    _partial_unique_index(
        ReferralCode.__table__,
        "uq_referral_codes_one_active_per_customer",
        [ReferralCode.__table__.c.customer_id],
        "active = true",
    )


apply_model_constraints()
