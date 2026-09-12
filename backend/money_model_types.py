"""Fixed-precision persistence types for financial and loyalty values.

The public API can keep its current numeric JSON contract while persistence is
strictly decimal. Legacy callers that still pass ``float`` are normalized at
the SQLAlchemy bind boundary so binary floating-point artifacts do not leak
into PostgreSQL NUMERIC columns.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any

from sqlalchemy import Numeric
from sqlalchemy.types import TypeDecorator

from .database import Base


MONEY_PRECISION = 20
MONEY_SCALE = 2
POINTS_SCALE = 4

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
    ("promo_codes", "discount_value"),
    ("orders", "loyalty_points_redeemed"),
    ("crm_profiles", "loyalty_points"),
    ("loyalty_transactions", "points_delta"),
    ("referral_codes", "reward_points"),
    ("loyalty_redemption_holds", "points"),
)


class FixedDecimal(TypeDecorator):
    """NUMERIC that always binds and returns quantized ``Decimal`` values."""

    impl = Numeric
    cache_ok = True

    def __init__(self, *, precision: int = MONEY_PRECISION, scale: int = MONEY_SCALE):
        self.precision = int(precision)
        self.scale = int(scale)
        self.quantum = Decimal("1").scaleb(-self.scale)
        super().__init__(precision=self.precision, scale=self.scale, asdecimal=True)

    @property
    def python_type(self):
        return Decimal

    def _normalize(self, value: Any) -> Decimal:
        try:
            decimal_value = value if isinstance(value, Decimal) else Decimal(str(value))
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise ValueError("Fixed-precision value must be numeric") from exc
        if not decimal_value.is_finite():
            raise ValueError("Fixed-precision value must be finite")
        return decimal_value.quantize(self.quantum, rounding=ROUND_HALF_UP)

    def process_bind_param(self, value: Any, dialect):
        if value is None:
            return None
        return self._normalize(value)

    def process_result_value(self, value: Any, dialect):
        if value is None:
            return None
        return self._normalize(value)


def _set_decimal_type(table_name: str, column_name: str, *, scale: int) -> None:
    table = Base.metadata.tables.get(table_name)
    if table is None:
        raise RuntimeError(f"Money type target table is not registered: {table_name}")
    column = table.c.get(column_name)
    if column is None:
        raise RuntimeError(f"Money type target column is not registered: {table_name}.{column_name}")
    column.type = FixedDecimal(precision=MONEY_PRECISION, scale=scale)


def apply_money_model_types() -> None:
    """Apply decimal persistence to all transactional amount/points columns."""

    # Register model modules that own financial columns outside backend.models.
    from . import catalog_intent_models as _catalog_intent_models  # noqa: F401
    from . import catalog_models as _catalog_models  # noqa: F401
    from . import models as _models  # noqa: F401

    for table_name, column_name in MONEY_COLUMNS:
        _set_decimal_type(table_name, column_name, scale=MONEY_SCALE)
    for table_name, column_name in POINTS_COLUMNS:
        _set_decimal_type(table_name, column_name, scale=POINTS_SCALE)
