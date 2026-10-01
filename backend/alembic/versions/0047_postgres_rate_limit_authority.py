"""PostgreSQL distributed rate-limit authority.

Revision ID: 0047_postgres_rate_limit_authority
Revises: 0046_moysklad_return_disposition
Create Date: 2026-10-01
"""

from alembic import op
import sqlalchemy as sa


revision = "0047_postgres_rate_limit_authority"
down_revision = "0046_moysklad_return_disposition"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "rate_limit_hits",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("bucket_key", sa.String(length=512), nullable=False),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("clock_timestamp()"),
        ),
    )
    op.create_index(
        "ix_rate_limit_hits_bucket_time",
        "rate_limit_hits",
        ["bucket_key", "occurred_at"],
    )
    op.create_index(
        "ix_rate_limit_hits_occurred_at",
        "rate_limit_hits",
        ["occurred_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_rate_limit_hits_occurred_at", table_name="rate_limit_hits")
    op.drop_index("ix_rate_limit_hits_bucket_time", table_name="rate_limit_hits")
    op.drop_table("rate_limit_hits")
