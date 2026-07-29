"""store financial values with fixed precision

Revision ID: 0019_numeric_money_storage
Revises: 0018_delivery_shipment_integrity
Create Date: 2026-07-29
"""

from alembic import op
import sqlalchemy as sa

revision = "0019_numeric_money_storage"
down_revision = "0018_delivery_shipment_integrity"
branch_labels = None
depends_on = None


_MONEY_COLUMNS = {
    "products": ("price", "old_price"),
    "promo_codes": ("min_amount",),
    "orders": (
        "total_amount",
        "delivery_price",
        "discount_amount",
        "loyalty_discount_amount",
    ),
    "order_items": ("price",),
    "payments": ("amount",),
    "return_requests": ("refund_amount",),
    "delivery_zones": ("price",),
    "crm_profiles": ("total_spent", "average_order_value"),
    "payment_reconciliations": ("amount_local", "amount_provider"),
    "delivery_shipments": ("price",),
}

_POINT_COLUMNS = {
    "carts": ("loyalty_points_to_redeem",),
    "orders": ("loyalty_points_redeemed",),
    "crm_profiles": ("loyalty_points",),
    "loyalty_transactions": ("points_delta",),
    "referral_codes": ("reward_points",),
    "loyalty_redemption_holds": ("points",),
}

_RATE_COLUMNS = {
    "promo_codes": ("discount_value",),
}


def _normalize_float(table: str, column: str) -> None:
    op.execute(
        sa.text(
            f"""
            UPDATE {table}
            SET {column} = 0
            WHERE {column} IS NULL
               OR {column} = 'NaN'::double precision
               OR {column} = 'Infinity'::double precision
               OR {column} = '-Infinity'::double precision
            """
        )
    )


def _to_numeric(table: str, column: str, precision: int, scale: int) -> None:
    _normalize_float(table, column)
    op.alter_column(
        table,
        column,
        existing_type=sa.Float(),
        type_=sa.Numeric(precision=precision, scale=scale),
        existing_nullable=True,
        postgresql_using=f"round({column}::numeric, {scale})",
    )


def _to_float(table: str, column: str, precision: int, scale: int) -> None:
    op.alter_column(
        table,
        column,
        existing_type=sa.Numeric(precision=precision, scale=scale),
        type_=sa.Float(),
        existing_nullable=True,
        postgresql_using=f"{column}::double precision",
    )


def upgrade():
    for table, columns in _MONEY_COLUMNS.items():
        for column in columns:
            _to_numeric(table, column, 20, 2)
    for table, columns in _POINT_COLUMNS.items():
        for column in columns:
            _to_numeric(table, column, 20, 4)
    for table, columns in _RATE_COLUMNS.items():
        for column in columns:
            _to_numeric(table, column, 20, 4)


def downgrade():
    for table, columns in reversed(tuple(_RATE_COLUMNS.items())):
        for column in reversed(columns):
            _to_float(table, column, 20, 4)
    for table, columns in reversed(tuple(_POINT_COLUMNS.items())):
        for column in reversed(columns):
            _to_float(table, column, 20, 4)
    for table, columns in reversed(tuple(_MONEY_COLUMNS.items())):
        for column in reversed(columns):
            _to_float(table, column, 20, 2)
