"""Add one-time Telegram bootstrap evidence and revocable customer sessions.

Revision ID: 0039_customer_auth_replay_sessions
Revises: 0038_relational_evidence_integrity
Create Date: 2026-09-10
"""

from alembic import op
import sqlalchemy as sa


revision = "0039_customer_auth_replay_sessions"
down_revision = "0038_relational_evidence_integrity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "telegram_auth_consumptions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("assertion_digest", sa.String(length=64), nullable=False),
        sa.Column("query_id_digest", sa.String(length=64), nullable=True),
        sa.Column("auth_date_epoch", sa.BigInteger(), nullable=False),
        sa.Column("consumed_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("assertion_digest", name="uq_telegram_auth_consumptions_assertion_digest"),
        sa.UniqueConstraint("query_id_digest", name="uq_telegram_auth_consumptions_query_id_digest"),
    )
    op.create_index(
        "ix_telegram_auth_consumptions_auth_date_epoch",
        "telegram_auth_consumptions",
        ["auth_date_epoch"],
    )
    op.create_index(
        "ix_telegram_auth_consumptions_consumed_at",
        "telegram_auth_consumptions",
        ["consumed_at"],
    )

    op.create_table(
        "customer_sessions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "customer_id",
            sa.Integer(),
            sa.ForeignKey("customers.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("token_id_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("token_id_hash", name="uq_customer_sessions_token_id_hash"),
    )
    op.create_index("ix_customer_sessions_customer_id", "customer_sessions", ["customer_id"])
    op.create_index("ix_customer_sessions_expires_at", "customer_sessions", ["expires_at"])
    op.create_index("ix_customer_sessions_revoked_at", "customer_sessions", ["revoked_at"])
    op.create_index("ix_customer_sessions_created_at", "customer_sessions", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_customer_sessions_created_at", table_name="customer_sessions")
    op.drop_index("ix_customer_sessions_revoked_at", table_name="customer_sessions")
    op.drop_index("ix_customer_sessions_expires_at", table_name="customer_sessions")
    op.drop_index("ix_customer_sessions_customer_id", table_name="customer_sessions")
    op.drop_table("customer_sessions")

    op.drop_index("ix_telegram_auth_consumptions_consumed_at", table_name="telegram_auth_consumptions")
    op.drop_index("ix_telegram_auth_consumptions_auth_date_epoch", table_name="telegram_auth_consumptions")
    op.drop_table("telegram_auth_consumptions")
