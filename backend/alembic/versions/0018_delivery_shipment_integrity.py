"""enforce delivery shipment integrity

Revision ID: 0018_delivery_shipment_integrity
Revises: 0017_payment_creation_attempts
Create Date: 2026-07-29
"""

from alembic import op

revision = "0018_delivery_shipment_integrity"
down_revision = "0017_payment_creation_attempts"
branch_labels = None
depends_on = None


_VALID_STATUSES = (
    "created",
    "shipped",
    "delivery_failed",
    "delivered",
    "returned",
    "cancelled",
)


def upgrade():
    op.execute(
        """
        UPDATE delivery_shipments
        SET provider_code = CASE
                WHEN length(trim(coalesce(provider_code, ''))) = 0 THEN 'courier'
                ELSE lower(trim(provider_code))
            END,
            status = CASE
                WHEN lower(trim(coalesce(status, ''))) IN (
                    'created', 'shipped', 'delivery_failed', 'delivered', 'returned', 'cancelled'
                ) THEN lower(trim(status))
                ELSE 'created'
            END,
            price = CASE WHEN price IS NULL OR price < 0 THEN 0 ELSE price END,
            tracking_number = trim(coalesce(tracking_number, '')),
            raw_payload = coalesce(raw_payload, '{}'),
            updated_at = coalesce(updated_at, created_at, CURRENT_TIMESTAMP)
        """
    )
    op.execute(
        """
        WITH ranked AS (
            SELECT
                id,
                row_number() OVER (
                    PARTITION BY order_id
                    ORDER BY
                        CASE status
                            WHEN 'delivered' THEN 60
                            WHEN 'returned' THEN 50
                            WHEN 'shipped' THEN 40
                            WHEN 'delivery_failed' THEN 30
                            WHEN 'created' THEN 20
                            WHEN 'cancelled' THEN 10
                            ELSE 0
                        END DESC,
                        CASE WHEN length(trim(coalesce(tracking_number, ''))) > 0 THEN 1 ELSE 0 END DESC,
                        updated_at DESC NULLS LAST,
                        created_at DESC NULLS LAST,
                        id DESC
                ) AS row_number_for_order
            FROM delivery_shipments
        )
        DELETE FROM delivery_shipments
        WHERE id IN (
            SELECT id FROM ranked WHERE row_number_for_order > 1
        )
        """
    )
    op.create_unique_constraint(
        "uq_delivery_shipments_order_id",
        "delivery_shipments",
        ["order_id"],
    )
    op.create_check_constraint(
        "ck_delivery_shipments_price_nonnegative",
        "delivery_shipments",
        "price >= 0",
    )
    op.create_check_constraint(
        "ck_delivery_shipments_provider_nonempty",
        "delivery_shipments",
        "length(trim(provider_code)) > 0",
    )
    op.create_check_constraint(
        "ck_delivery_shipments_status_valid",
        "delivery_shipments",
        "status IN ('created', 'shipped', 'delivery_failed', 'delivered', 'returned', 'cancelled')",
    )


def downgrade():
    op.drop_constraint(
        "ck_delivery_shipments_status_valid",
        "delivery_shipments",
        type_="check",
    )
    op.drop_constraint(
        "ck_delivery_shipments_provider_nonempty",
        "delivery_shipments",
        type_="check",
    )
    op.drop_constraint(
        "ck_delivery_shipments_price_nonnegative",
        "delivery_shipments",
        type_="check",
    )
    op.drop_constraint(
        "uq_delivery_shipments_order_id",
        "delivery_shipments",
        type_="unique",
    )
