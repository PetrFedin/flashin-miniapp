"""constrain promo definitions

Revision ID: 0017_promo_definition_constraints
Revises: 0016_one_active_cart
Create Date: 2026-08-01
"""

from alembic import op
import sqlalchemy as sa


revision = "0017_promo_definition_constraints"
down_revision = "0016_one_active_cart"
branch_labels = None
depends_on = None


_TYPE_CONSTRAINT = "ck_promo_codes_discount_type"
_PERCENT_CONSTRAINT = "ck_promo_codes_percent_bounded"


def upgrade():
    op.execute(
        sa.text(
            """
            UPDATE promo_codes
            SET
                active = false,
                discount_type = 'fixed',
                discount_value = 0
            WHERE discount_type IS NULL
               OR discount_type NOT IN ('percent', 'fixed')
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE promo_codes
            SET
                active = false,
                discount_value = 100
            WHERE discount_type = 'percent'
              AND discount_value > 100
            """
        )
    )

    op.create_check_constraint(
        _TYPE_CONSTRAINT,
        "promo_codes",
        "discount_type IN ('percent', 'fixed')",
    )
    op.create_check_constraint(
        _PERCENT_CONSTRAINT,
        "promo_codes",
        "discount_type <> 'percent' OR discount_value <= 100",
    )


def downgrade():
    op.drop_constraint(_PERCENT_CONSTRAINT, "promo_codes", type_="check")
    op.drop_constraint(_TYPE_CONSTRAINT, "promo_codes", type_="check")
