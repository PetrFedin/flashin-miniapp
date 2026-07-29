"""transaction integrity constraints

Revision ID: 0010_transaction_integrity_constraints
Revises: 0009_security_payment_delivery_media_hardening
Create Date: 2026-07-29

This revision deliberately refuses to delete or merge financial records. If old
rows violate a new invariant, the migration stops with a precise message so the
conflict can be reconciled and audited before constraints are enabled.
"""

from alembic import op
import sqlalchemy as sa

revision = "0010_transaction_integrity_constraints"
down_revision = "0009_security_payment_delivery_media_hardening"
branch_labels = None
depends_on = None


def _assert_empty(sql: str, message: str) -> None:
    count = op.get_bind().execute(sa.text(sql)).scalar_one()
    if count:
        raise RuntimeError(f"Integrity migration blocked: {message} ({count} conflict group(s)/row(s))")


def _validate_existing_data() -> None:
    _assert_empty(
        """
        SELECT count(*) FROM product_variants
        WHERE stock_qty < 0 OR reserved_qty < 0 OR reserved_qty > stock_qty
        """,
        "product variants contain invalid stock or reservation quantities",
    )
    _assert_empty(
        "SELECT count(*) FROM cart_items WHERE quantity <= 0 OR quantity > 10",
        "cart items contain invalid quantities",
    )
    _assert_empty(
        "SELECT count(*) FROM order_items WHERE quantity <= 0 OR price < 0",
        "order items contain invalid quantity or price values",
    )
    _assert_empty(
        """
        SELECT count(*) FROM orders
        WHERE total_amount < 0
           OR delivery_price < 0
           OR discount_amount < 0
           OR loyalty_points_redeemed < 0
           OR loyalty_discount_amount < 0
        """,
        "orders contain negative financial values",
    )
    _assert_empty(
        """
        SELECT count(*) FROM promo_codes
        WHERE discount_value < 0
           OR min_amount < 0
           OR max_uses < 0
           OR used_count < 0
           OR (max_uses > 0 AND used_count > max_uses)
        """,
        "promo codes contain invalid limits or counters",
    )
    _assert_empty(
        "SELECT count(*) FROM payments WHERE amount <= 0",
        "payments contain non-positive amounts",
    )
    _assert_empty(
        "SELECT count(*) FROM crm_profiles WHERE loyalty_points < 0",
        "CRM profiles contain negative loyalty balances",
    )
    _assert_empty(
        "SELECT count(*) FROM loyalty_redemption_holds WHERE points <= 0",
        "loyalty holds contain non-positive points",
    )

    _assert_empty(
        """
        SELECT count(*) FROM (
            SELECT customer_id
            FROM carts
            WHERE status = 'active'
            GROUP BY customer_id
            HAVING count(*) > 1
        ) conflicts
        """,
        "customers have more than one active cart",
    )
    _assert_empty(
        """
        SELECT count(*) FROM (
            SELECT provider, provider_payment_id
            FROM payments
            WHERE provider_payment_id <> ''
            GROUP BY provider, provider_payment_id
            HAVING count(*) > 1
        ) conflicts
        """,
        "provider payment IDs are duplicated",
    )
    _assert_empty(
        """
        SELECT count(*) FROM (
            SELECT provider, provider_payment_id, event_type
            FROM payment_events
            WHERE provider_payment_id <> '' AND event_type <> ''
            GROUP BY provider, provider_payment_id, event_type
            HAVING count(*) > 1
        ) conflicts
        """,
        "payment webhook events are duplicated",
    )
    _assert_empty(
        """
        SELECT count(*) FROM (
            SELECT role, permission
            FROM admin_role_permissions
            GROUP BY role, permission
            HAVING count(*) > 1
        ) conflicts
        """,
        "admin role permissions are duplicated",
    )
    _assert_empty(
        """
        SELECT count(*) FROM (
            SELECT order_id
            FROM fulfillment_tasks
            GROUP BY order_id
            HAVING count(*) > 1
        ) conflicts
        """,
        "orders have duplicate fulfillment tasks",
    )
    _assert_empty(
        """
        SELECT count(*) FROM (
            SELECT session_token_hash
            FROM admin_sessions
            GROUP BY session_token_hash
            HAVING count(*) > 1
        ) conflicts
        """,
        "admin session token hashes are duplicated",
    )
    _assert_empty(
        """
        SELECT count(*) FROM (
            SELECT token_hash
            FROM admin_password_resets
            GROUP BY token_hash
            HAVING count(*) > 1
        ) conflicts
        """,
        "admin password reset token hashes are duplicated",
    )
    _assert_empty(
        """
        SELECT count(*) FROM (
            SELECT customer_id, order_id, reason
            FROM loyalty_transactions
            WHERE order_id IS NOT NULL
              AND reason IN ('order_paid', 'loyalty_redeemed', 'referral_reward', 'loyalty_refund')
            GROUP BY customer_id, order_id, reason
            HAVING count(*) > 1
        ) conflicts
        """,
        "order-linked loyalty transactions are duplicated",
    )
    _assert_empty(
        """
        SELECT count(*) FROM (
            SELECT customer_id, cart_id
            FROM loyalty_redemption_holds
            WHERE cart_id IS NOT NULL AND status = 'reserved'
            GROUP BY customer_id, cart_id
            HAVING count(*) > 1
        ) conflicts
        """,
        "carts have duplicate reserved loyalty holds",
    )
    _assert_empty(
        """
        SELECT count(*) FROM (
            SELECT customer_id, order_id
            FROM loyalty_redemption_holds
            WHERE order_id IS NOT NULL AND status IN ('committed', 'refunded')
            GROUP BY customer_id, order_id
            HAVING count(*) > 1
        ) conflicts
        """,
        "orders have duplicate committed/refunded loyalty holds",
    )
    _assert_empty(
        """
        SELECT count(*) FROM (
            SELECT customer_id
            FROM referral_codes
            WHERE active = true
            GROUP BY customer_id
            HAVING count(*) > 1
        ) conflicts
        """,
        "customers have duplicate active referral codes",
    )


def upgrade():
    _validate_existing_data()

    op.create_check_constraint(
        "ck_product_variants_stock_nonnegative",
        "product_variants",
        "stock_qty >= 0",
    )
    op.create_check_constraint(
        "ck_product_variants_reserved_nonnegative",
        "product_variants",
        "reserved_qty >= 0",
    )
    op.create_check_constraint(
        "ck_product_variants_reserved_within_stock",
        "product_variants",
        "reserved_qty <= stock_qty",
    )
    op.create_check_constraint("ck_cart_items_quantity_positive", "cart_items", "quantity > 0")
    op.create_check_constraint("ck_cart_items_quantity_limit", "cart_items", "quantity <= 10")
    op.create_check_constraint("ck_order_items_quantity_positive", "order_items", "quantity > 0")
    op.create_check_constraint("ck_order_items_price_nonnegative", "order_items", "price >= 0")
    op.create_check_constraint("ck_orders_total_nonnegative", "orders", "total_amount >= 0")
    op.create_check_constraint("ck_orders_delivery_nonnegative", "orders", "delivery_price >= 0")
    op.create_check_constraint("ck_orders_discount_nonnegative", "orders", "discount_amount >= 0")
    op.create_check_constraint(
        "ck_orders_loyalty_points_nonnegative",
        "orders",
        "loyalty_points_redeemed >= 0",
    )
    op.create_check_constraint(
        "ck_orders_loyalty_discount_nonnegative",
        "orders",
        "loyalty_discount_amount >= 0",
    )
    op.create_check_constraint(
        "ck_promo_codes_discount_nonnegative",
        "promo_codes",
        "discount_value >= 0",
    )
    op.create_check_constraint(
        "ck_promo_codes_min_amount_nonnegative",
        "promo_codes",
        "min_amount >= 0",
    )
    op.create_check_constraint(
        "ck_promo_codes_max_uses_nonnegative",
        "promo_codes",
        "max_uses >= 0",
    )
    op.create_check_constraint(
        "ck_promo_codes_used_count_nonnegative",
        "promo_codes",
        "used_count >= 0",
    )
    op.create_check_constraint(
        "ck_promo_codes_usage_within_limit",
        "promo_codes",
        "max_uses = 0 OR used_count <= max_uses",
    )
    op.create_check_constraint("ck_payments_amount_positive", "payments", "amount > 0")
    op.create_check_constraint(
        "ck_crm_profiles_loyalty_nonnegative",
        "crm_profiles",
        "loyalty_points >= 0",
    )
    op.create_check_constraint(
        "ck_loyalty_holds_points_positive",
        "loyalty_redemption_holds",
        "points > 0",
    )

    op.create_unique_constraint(
        "uq_admin_role_permissions_role_permission",
        "admin_role_permissions",
        ["role", "permission"],
    )
    op.create_unique_constraint(
        "uq_fulfillment_tasks_order_id",
        "fulfillment_tasks",
        ["order_id"],
    )
    op.create_unique_constraint(
        "uq_admin_sessions_token_hash",
        "admin_sessions",
        ["session_token_hash"],
    )
    op.create_unique_constraint(
        "uq_admin_password_resets_token_hash",
        "admin_password_resets",
        ["token_hash"],
    )

    op.create_index(
        "uq_carts_one_active_per_customer",
        "carts",
        ["customer_id"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )
    op.create_index(
        "uq_payments_provider_payment_id",
        "payments",
        ["provider", "provider_payment_id"],
        unique=True,
        postgresql_where=sa.text("provider_payment_id <> ''"),
    )
    op.create_index(
        "uq_payment_events_provider_event",
        "payment_events",
        ["provider", "provider_payment_id", "event_type"],
        unique=True,
        postgresql_where=sa.text("provider_payment_id <> '' AND event_type <> ''"),
    )
    op.create_index(
        "uq_loyalty_transactions_order_reason",
        "loyalty_transactions",
        ["customer_id", "order_id", "reason"],
        unique=True,
        postgresql_where=sa.text(
            "order_id IS NOT NULL AND reason IN "
            "('order_paid', 'loyalty_redeemed', 'referral_reward', 'loyalty_refund')"
        ),
    )
    op.create_index(
        "uq_loyalty_holds_reserved_cart",
        "loyalty_redemption_holds",
        ["customer_id", "cart_id"],
        unique=True,
        postgresql_where=sa.text("cart_id IS NOT NULL AND status = 'reserved'"),
    )
    op.create_index(
        "uq_loyalty_holds_order",
        "loyalty_redemption_holds",
        ["customer_id", "order_id"],
        unique=True,
        postgresql_where=sa.text(
            "order_id IS NOT NULL AND status IN ('committed', 'refunded')"
        ),
    )
    op.create_index(
        "uq_referral_codes_one_active_per_customer",
        "referral_codes",
        ["customer_id"],
        unique=True,
        postgresql_where=sa.text("active = true"),
    )


def downgrade():
    op.drop_index("uq_referral_codes_one_active_per_customer", table_name="referral_codes")
    op.drop_index("uq_loyalty_holds_order", table_name="loyalty_redemption_holds")
    op.drop_index("uq_loyalty_holds_reserved_cart", table_name="loyalty_redemption_holds")
    op.drop_index("uq_loyalty_transactions_order_reason", table_name="loyalty_transactions")
    op.drop_index("uq_payment_events_provider_event", table_name="payment_events")
    op.drop_index("uq_payments_provider_payment_id", table_name="payments")
    op.drop_index("uq_carts_one_active_per_customer", table_name="carts")

    op.drop_constraint("uq_admin_password_resets_token_hash", "admin_password_resets", type_="unique")
    op.drop_constraint("uq_admin_sessions_token_hash", "admin_sessions", type_="unique")
    op.drop_constraint("uq_fulfillment_tasks_order_id", "fulfillment_tasks", type_="unique")
    op.drop_constraint(
        "uq_admin_role_permissions_role_permission",
        "admin_role_permissions",
        type_="unique",
    )

    op.drop_constraint("ck_loyalty_holds_points_positive", "loyalty_redemption_holds", type_="check")
    op.drop_constraint("ck_crm_profiles_loyalty_nonnegative", "crm_profiles", type_="check")
    op.drop_constraint("ck_payments_amount_positive", "payments", type_="check")
    op.drop_constraint("ck_promo_codes_usage_within_limit", "promo_codes", type_="check")
    op.drop_constraint("ck_promo_codes_used_count_nonnegative", "promo_codes", type_="check")
    op.drop_constraint("ck_promo_codes_max_uses_nonnegative", "promo_codes", type_="check")
    op.drop_constraint("ck_promo_codes_min_amount_nonnegative", "promo_codes", type_="check")
    op.drop_constraint("ck_promo_codes_discount_nonnegative", "promo_codes", type_="check")
    op.drop_constraint("ck_orders_loyalty_discount_nonnegative", "orders", type_="check")
    op.drop_constraint("ck_orders_loyalty_points_nonnegative", "orders", type_="check")
    op.drop_constraint("ck_orders_discount_nonnegative", "orders", type_="check")
    op.drop_constraint("ck_orders_delivery_nonnegative", "orders", type_="check")
    op.drop_constraint("ck_orders_total_nonnegative", "orders", type_="check")
    op.drop_constraint("ck_order_items_price_nonnegative", "order_items", type_="check")
    op.drop_constraint("ck_order_items_quantity_positive", "order_items", type_="check")
    op.drop_constraint("ck_cart_items_quantity_limit", "cart_items", type_="check")
    op.drop_constraint("ck_cart_items_quantity_positive", "cart_items", type_="check")
    op.drop_constraint(
        "ck_product_variants_reserved_within_stock",
        "product_variants",
        type_="check",
    )
    op.drop_constraint(
        "ck_product_variants_reserved_nonnegative",
        "product_variants",
        type_="check",
    )
    op.drop_constraint(
        "ck_product_variants_stock_nonnegative",
        "product_variants",
        type_="check",
    )
