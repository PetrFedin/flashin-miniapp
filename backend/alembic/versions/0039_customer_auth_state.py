"""Add replay-safe Telegram assertions and revocable customer sessions.

Revision ID: 0039_customer_auth_state
Revises: 0038_relational_evidence_integrity
Create Date: 2026-09-12

The migration stores only keyed fingerprints for Telegram bootstrap assertions,
JWT session identifiers and JTIs. Raw initData, Telegram query_id/signature
material and bearer tokens are intentionally excluded from the schema.
"""

from alembic import op
import sqlalchemy as sa


revision = "0039_customer_auth_state"
down_revision = "0038_relational_evidence_integrity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "telegram_auth_assertions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("assertion_hash", sa.String(length=64), nullable=False),
        sa.Column("payload_hash", sa.String(length=64), nullable=False),
        sa.Column("auth_date", sa.Integer(), nullable=False),
        sa.Column("consumed_at", sa.DateTime(), nullable=False),
        sa.Column("retain_until", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "assertion_hash",
            name="uq_telegram_auth_assertions_hash",
        ),
    )
    op.create_index(
        "ix_telegram_auth_assertions_retain_until",
        "telegram_auth_assertions",
        ["retain_until"],
        unique=False,
    )

    op.create_table(
        "customer_sessions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("customer_id", sa.Integer(), nullable=False),
        sa.Column("session_id_hash", sa.String(length=64), nullable=False),
        sa.Column("jti_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.Column("revoke_reason", sa.String(length=120), nullable=False),
        sa.ForeignKeyConstraint(["customer_id"], ["customers.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("jti_hash", name="uq_customer_sessions_jti_hash"),
        sa.UniqueConstraint(
            "session_id_hash",
            name="uq_customer_sessions_sid_hash",
        ),
    )
    op.create_index(
        "ix_customer_sessions_customer_id",
        "customer_sessions",
        ["customer_id"],
        unique=False,
    )
    op.create_index(
        "ix_customer_sessions_expires_at",
        "customer_sessions",
        ["expires_at"],
        unique=False,
    )

    op.create_table(
        "customer_auth_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("customer_id", sa.Integer(), nullable=False),
        sa.Column("session_id", sa.Integer(), nullable=True),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["customer_id"], ["customers.id"]),
        sa.ForeignKeyConstraint(["session_id"], ["customer_sessions.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_customer_auth_events_customer_id",
        "customer_auth_events",
        ["customer_id"],
        unique=False,
    )
    op.create_index(
        "ix_customer_auth_events_session_id",
        "customer_auth_events",
        ["session_id"],
        unique=False,
    )
    op.create_index(
        "ix_customer_auth_events_event_type",
        "customer_auth_events",
        ["event_type"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_customer_auth_events_event_type", table_name="customer_auth_events")
    op.drop_index("ix_customer_auth_events_session_id", table_name="customer_auth_events")
    op.drop_index("ix_customer_auth_events_customer_id", table_name="customer_auth_events")
    op.drop_table("customer_auth_events")

    op.drop_index("ix_customer_sessions_expires_at", table_name="customer_sessions")
    op.drop_index("ix_customer_sessions_customer_id", table_name="customer_sessions")
    op.drop_table("customer_sessions")

    op.drop_index(
        "ix_telegram_auth_assertions_retain_until",
        table_name="telegram_auth_assertions",
    )
    op.drop_table("telegram_auth_assertions")
