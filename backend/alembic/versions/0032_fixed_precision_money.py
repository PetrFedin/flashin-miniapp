"""Store transactional money and loyalty values at fixed precision.

Revision ID: 0032_fixed_precision_money
Revises: 0031_payment_creation_attempt_integrity
Create Date: 2026-08-20
"""

from alembic import op
import sqlalchemy as sa


revision = "0032_fixed_precision_money"
down_revision = "0031_payment_creation_attempt_integrity"
branch_labels = None
depends_on = None


MONEY_COLUMNS: tuple[tuple[str, str], ...] = (
    ("products", "price"),
    ("products", "old_price"),
    ("promo_codes", "min_amount"),
    ("orders", "total_amount"),
    ("orders", "delivery_price"),
    ("orders", "discount_amount"),
    ("orders", "loyalty_discount_amount"),
    ("order_items", "price"),
    ("payments", "amount"),
    ("return_requests", "refund_amount"),
    ("delivery_zones", "price"),
    ("crm_profiles", "total_spent"),
    ("crm_profiles", "average_order_value"),
    ("payment_reconciliations", "amount_local"),
    ("payment_reconciliations", "amount_provider"),
    ("delivery_shipments", "price"),
    ("product_merchandising", "promo_price"),
    ("product_external_availability", "price"),
    ("product_intent_requests", "quote_amount"),
)

POINTS_COLUMNS: tuple[tuple[str, str], ...] = (
    ("carts", "loyalty_points_to_redeem"),
    # discount_value is dual-use: fractional percent or fixed discount.
    # Scale 4 preserves fractional percentages while fixed discounts continue
    # to be rounded to currency precision by the pricing domain.
    ("promo_codes", "discount_value"),
    ("orders", "loyalty_points_redeemed"),
    ("crm_profiles", "loyalty_points"),
    ("loyalty_transactions", "points_delta"),
    ("referral_codes", "reward_points"),
    ("loyalty_redemption_holds", "points"),
)


def _to_numeric(table: str, column: str, scale: int) -> None:
    op.alter_column(
        table,
        column,
        existing_type=sa.Float(),
        type_=sa.Numeric(precision=20, scale=scale, asdecimal=True),
        postgresql_using=f"ROUND({column}::numeric, {scale})",
    )


def _to_float(table: str, column: str, scale: int) -> None:
    op.alter_column(
        table,
        column,
        existing_type=sa.Numeric(precision=20, scale=scale, asdecimal=True),
        type_=sa.Float(),
        postgresql_using=f"{column}::double precision",
    )


def upgrade() -> None:
    for table, column in MONEY_COLUMNS:
        _to_numeric(table, column, 2)
    for table, column in POINTS_COLUMNS:
        _to_numeric(table, column, 4)


def downgrade() -> None:
    for table, column in reversed(POINTS_COLUMNS):
        _to_float(table, column, 4)
    for table, column in reversed(MONEY_COLUMNS):
        _to_float(table, column, 2)
