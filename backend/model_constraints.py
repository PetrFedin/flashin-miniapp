"""Database constraints that must exist in both create_all and Alembic metadata.

Production applies the same rules through Alembic revisions. Keeping them in
metadata prevents local/test databases created with ``Base.metadata.create_all``
from being weaker than PostgreSQL production.
"""

from sqlalchemy import (
    CheckConstraint,
    DDL,
    ForeignKeyConstraint,
    Index,
    UniqueConstraint,
    event,
    text,
)
from sqlalchemy.orm import configure_mappers

from .checkout_models import CheckoutAttempt
from .money_model_types import apply_money_model_types
from .models import (
    AdminPasswordReset,
    AdminRolePermission,
    AdminSession,
    Cart,
    CartItem,
    CrmProfile,
    FulfillmentTask,
    FulfillmentTaskItem,
    InventoryMovement,
    LoyaltyRedemptionHold,
    LoyaltyTransaction,
    Order,
    OrderItem,
    Payment,
    PaymentEvent,
    PaymentReconciliation,
    ProductVariant,
    PromoCode,
    ReferralAttribution,
    ReferralCode,
    ReturnRequest,
    SupportTicket,
    WebhookDestination,
    WebhookOutbox,
)
from .pilot_models import PilotOrderSlot


def _constraint_names(table) -> set[str]:
    return {constraint.name for constraint in table.constraints if constraint.name}


def _index_names(table) -> set[str]:
    return {index.name for index in table.indexes if index.name}


def _check(table, name: str, expression: str) -> None:
    if name not in _constraint_names(table):
        table.append_constraint(CheckConstraint(expression, name=name))


def _replace_check(table, name: str, expression: str) -> None:
    for constraint in list(table.constraints):
        if constraint.name == name:
            table.constraints.remove(constraint)
    table.append_constraint(CheckConstraint(expression, name=name))


def _unique(table, name: str, *columns: str) -> None:
    if name not in _constraint_names(table):
        table.append_constraint(UniqueConstraint(*columns, name=name))


def _foreign_key(
    table,
    name: str,
    local_columns: tuple[str, ...],
    remote_columns: tuple[str, ...],
    *,
    ondelete: str | None = None,
) -> None:
    if name not in _constraint_names(table):
        table.append_constraint(
            ForeignKeyConstraint(
                local_columns,
                remote_columns,
                name=name,
                ondelete=ondelete,
            )
        )


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


def apply_model_constraints() -> None:
    _check(ProductVariant.__table__, "ck_product_variants_stock_nonnegative", "stock_qty >= 0")
    _check(ProductVariant.__table__, "ck_product_variants_reserved_nonnegative", "reserved_qty >= 0")
    _check(ProductVariant.__table__, "ck_product_variants_reserved_within_stock", "reserved_qty <= stock_qty")
    _replace_check(
        InventoryMovement.__table__,
        "ck_inventory_movements_kind",
        "kind IN ('reserve', 'release', 'commit', 'return')",
    )

    _check(CartItem.__table__, "ck_cart_items_quantity_positive", "quantity > 0")
    _check(CartItem.__table__, "ck_cart_items_quantity_limit", "quantity <= 10")

    _check(OrderItem.__table__, "ck_order_items_quantity_positive", "quantity > 0")
    _check(OrderItem.__table__, "ck_order_items_price_nonnegative", "price >= 0")

    _check(Order.__table__, "ck_orders_total_nonnegative", "total_amount >= 0")
    _check(Order.__table__, "ck_orders_delivery_nonnegative", "delivery_price >= 0")
    _check(Order.__table__, "ck_orders_discount_nonnegative", "discount_amount >= 0")
    _check(Order.__table__, "ck_orders_loyalty_points_nonnegative", "loyalty_points_redeemed >= 0")
    _check(Order.__table__, "ck_orders_loyalty_discount_nonnegative", "loyalty_discount_amount >= 0")

    _check(PromoCode.__table__, "ck_promo_codes_discount_nonnegative", "discount_value >= 0")
    _check(PromoCode.__table__, "ck_promo_codes_min_amount_nonnegative", "min_amount >= 0")
    _check(PromoCode.__table__, "ck_promo_codes_max_uses_nonnegative", "max_uses >= 0")
    _check(PromoCode.__table__, "ck_promo_codes_used_count_nonnegative", "used_count >= 0")
    _check(
        PromoCode.__table__,
        "ck_promo_codes_discount_type",
        "discount_type IN ('percent', 'fixed')",
    )
    _check(
        PromoCode.__table__,
        "ck_promo_codes_percent_bounded",
        "discount_type <> 'percent' OR discount_value <= 100",
    )
    _check(
        PromoCode.__table__,
        "ck_promo_codes_usage_within_limit",
        "max_uses = 0 OR used_count <= max_uses",
    )

    _check(Payment.__table__, "ck_payments_amount_positive", "amount > 0")
    _check(ReturnRequest.__table__, "ck_return_requests_refund_nonnegative", "refund_amount >= 0")
    _check(CrmProfile.__table__, "ck_crm_profiles_loyalty_nonnegative", "loyalty_points >= 0")
    _check(LoyaltyRedemptionHold.__table__, "ck_loyalty_holds_points_positive", "points > 0")
    _check(WebhookOutbox.__table__, "ck_webhook_outbox_attempts_nonnegative", "attempts >= 0")
    _check(WebhookDestination.__table__, "ck_webhook_destinations_url_nonempty", "length(trim(url)) > 0")
    _check(
        WebhookDestination.__table__,
        "ck_webhook_destinations_event_type_nonempty",
        "length(trim(event_type)) > 0",
    )

    _unique(
        AdminRolePermission.__table__,
        "uq_admin_role_permissions_role_permission",
        "role",
        "permission",
    )
    _unique(FulfillmentTask.__table__, "uq_fulfillment_tasks_order_id", "order_id")
    _unique(AdminSession.__table__, "uq_admin_sessions_token_hash", "session_token_hash")
    _unique(AdminPasswordReset.__table__, "uq_admin_password_resets_token_hash", "token_hash")
    _unique(
        WebhookDestination.__table__,
        "uq_webhook_destinations_url_event_type",
        "url",
        "event_type",
    )
    _unique(Order.__table__, "uq_orders_id_customer_id", "id", "customer_id")
    _unique(Cart.__table__, "uq_carts_id_customer_id", "id", "customer_id")
    _unique(
        ProductVariant.__table__,
        "uq_product_variants_id_product_id",
        "id",
        "product_id",
    )
    _unique(Payment.__table__, "uq_payments_id_order_id", "id", "order_id")

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


def apply_customer_owned_reference_constraints() -> None:
    """Add true customer-owned cross-table invariants after ORM joins are configured.

    Existing relationships intentionally continue to use their original
    single-column foreign keys. Configuring the mappers before these composite
    constraints are attached prevents the additional database-level path from
    making relationship inference ambiguous while keeping create_all metadata
    as strict as production Alembic migrations.

    ``LoyaltyTransaction.order_id`` is deliberately excluded: referral reward
    and referral refund rows credit/debit the referrer while pointing at the
    invited customer's source order, so that column is provenance rather than
    an ownership relation.
    """

    order_target = ("orders.id", "orders.customer_id")
    cart_target = ("carts.id", "carts.customer_id")

    _foreign_key(
        ReturnRequest.__table__,
        "fk_return_requests_order_customer",
        ("order_id", "customer_id"),
        order_target,
    )
    _foreign_key(
        SupportTicket.__table__,
        "fk_support_tickets_order_customer",
        ("order_id", "customer_id"),
        order_target,
    )
    _foreign_key(
        LoyaltyRedemptionHold.__table__,
        "fk_loyalty_redemption_holds_order_customer",
        ("order_id", "customer_id"),
        order_target,
    )
    _foreign_key(
        LoyaltyRedemptionHold.__table__,
        "fk_loyalty_redemption_holds_cart_customer",
        ("cart_id", "customer_id"),
        cart_target,
    )
    _foreign_key(
        CheckoutAttempt.__table__,
        "fk_checkout_attempts_order_customer",
        ("order_id", "customer_id"),
        order_target,
    )
    _foreign_key(
        CheckoutAttempt.__table__,
        "fk_checkout_attempts_cart_customer",
        ("cart_id", "customer_id"),
        cart_target,
    )
    _foreign_key(
        PilotOrderSlot.__table__,
        "fk_pilot_order_slots_order_customer",
        ("order_id", "customer_id"),
        order_target,
        ondelete="CASCADE",
    )
    _foreign_key(
        ReferralAttribution.__table__,
        "fk_referral_attributions_rewarded_order_invited_customer",
        ("rewarded_order_id", "invited_customer_id"),
        order_target,
    )


def apply_product_variant_reference_constraints() -> None:
    """Keep denormalized product/variant pairs internally consistent."""

    variant_target = ("product_variants.id", "product_variants.product_id")
    _foreign_key(
        CartItem.__table__,
        "fk_cart_items_variant_product",
        ("variant_id", "product_id"),
        variant_target,
    )
    _foreign_key(
        OrderItem.__table__,
        "fk_order_items_variant_product",
        ("variant_id", "product_id"),
        variant_target,
    )


def apply_payment_reconciliation_reference_constraints() -> None:
    """Bind local reconciliation references without constraining provider evidence."""

    _foreign_key(
        PaymentReconciliation.__table__,
        "fk_payment_reconciliations_payment_order",
        ("payment_id", "order_id"),
        ("payments.id", "payments.order_id"),
    )


def register_fulfillment_task_item_order_triggers() -> None:
    """Make create_all databases enforce the same same-order rule as Alembic.

    ``FulfillmentTaskItem`` does not duplicate ``order_id``, so this invariant
    cannot be represented as a normal composite foreign key without changing
    the data model. Production uses a PostgreSQL trigger. Tests/local SQLite
    receive an equivalent trigger through SQLAlchemy DDL events.
    """

    table = FulfillmentTaskItem.__table__
    marker = "fulfillment_task_item_same_order_triggers_registered"
    if table.info.get(marker):
        return
    table.info[marker] = True

    sqlite_insert = DDL(
        """
        CREATE TRIGGER IF NOT EXISTS trg_fulfillment_task_items_same_order_insert
        BEFORE INSERT ON fulfillment_task_items
        FOR EACH ROW
        WHEN NOT EXISTS (
            SELECT 1
            FROM fulfillment_tasks AS task
            JOIN order_items AS order_item
              ON order_item.id = NEW.order_item_id
            WHERE task.id = NEW.task_id
              AND task.order_id = order_item.order_id
        )
        BEGIN
            SELECT RAISE(ABORT, 'fulfillment task item must reference an order item from the same order');
        END
        """
    ).execute_if(dialect="sqlite")
    sqlite_update = DDL(
        """
        CREATE TRIGGER IF NOT EXISTS trg_fulfillment_task_items_same_order_update
        BEFORE UPDATE OF task_id, order_item_id ON fulfillment_task_items
        FOR EACH ROW
        WHEN NOT EXISTS (
            SELECT 1
            FROM fulfillment_tasks AS task
            JOIN order_items AS order_item
              ON order_item.id = NEW.order_item_id
            WHERE task.id = NEW.task_id
              AND task.order_id = order_item.order_id
        )
        BEGIN
            SELECT RAISE(ABORT, 'fulfillment task item must reference an order item from the same order');
        END
        """
    ).execute_if(dialect="sqlite")
    postgres_function = DDL(
        """
        CREATE OR REPLACE FUNCTION enforce_fulfillment_task_item_same_order()
        RETURNS trigger AS $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1
                FROM fulfillment_tasks AS task
                JOIN order_items AS order_item
                  ON order_item.id = NEW.order_item_id
                WHERE task.id = NEW.task_id
                  AND task.order_id = order_item.order_id
            ) THEN
                RAISE EXCEPTION 'fulfillment task item must reference an order item from the same order';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    ).execute_if(dialect="postgresql")
    postgres_trigger = DDL(
        """
        CREATE TRIGGER trg_fulfillment_task_items_same_order
        BEFORE INSERT OR UPDATE OF task_id, order_item_id
        ON fulfillment_task_items
        FOR EACH ROW
        EXECUTE FUNCTION enforce_fulfillment_task_item_same_order()
        """
    ).execute_if(dialect="postgresql")
    postgres_drop_function = DDL(
        "DROP FUNCTION IF EXISTS enforce_fulfillment_task_item_same_order()"
    ).execute_if(dialect="postgresql")

    event.listen(table, "after_create", sqlite_insert)
    event.listen(table, "after_create", sqlite_update)
    event.listen(table, "after_create", postgres_function)
    event.listen(table, "after_create", postgres_trigger)
    event.listen(table, "after_drop", postgres_drop_function)


apply_money_model_types()
apply_model_constraints()
configure_mappers()
apply_customer_owned_reference_constraints()
apply_product_variant_reference_constraints()
apply_payment_reconciliation_reference_constraints()
register_fulfillment_task_item_order_triggers()
