"""Add merchandising, external availability, feedback and showroom booking.

Revision ID: 0028_catalog_merchandising
Revises: 0027_pilot_worker_heartbeats
Create Date: 2026-08-14
"""

from alembic import op
import sqlalchemy as sa


revision = "0028_catalog_merchandising"
down_revision = "0027_pilot_worker_heartbeats"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "product_merchandising",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("product_id", sa.Integer(), nullable=False),
        sa.Column("availability_status", sa.String(length=32), nullable=False, server_default="in_stock"),
        sa.Column("material", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("season", sa.String(length=120), nullable=False, server_default=""),
        sa.Column("badges_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("grid_rank", sa.Integer(), nullable=False, server_default="1000"),
        sa.Column("sale_starts_at", sa.DateTime(), nullable=True),
        sa.Column("sale_ends_at", sa.DateTime(), nullable=True),
        sa.Column("showroom_fitting_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "availability_status IN ('in_stock', 'preorder', 'made_to_order', 'out_of_stock')",
            name="ck_product_merchandising_availability",
        ),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("product_id", name="uq_product_merchandising_product"),
    )
    op.create_index("ix_product_merchandising_product_id", "product_merchandising", ["product_id"])
    op.create_index("ix_product_merchandising_grid_rank", "product_merchandising", ["grid_rank"])

    op.create_table(
        "product_videos",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("product_id", sa.Integer(), nullable=False),
        sa.Column("url", sa.String(length=2048), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_product_videos_product_id", "product_videos", ["product_id"])

    op.create_table(
        "product_external_availability",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("product_id", sa.Integer(), nullable=False),
        sa.Column("source_name", sa.String(length=255), nullable=False),
        sa.Column("url", sa.String(length=2048), nullable=False),
        sa.Column("availability_status", sa.String(length=32), nullable=False, server_default="in_stock"),
        sa.Column("price", sa.Float(), nullable=True),
        sa.Column("currency", sa.String(length=8), nullable=False, server_default="RUB"),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "availability_status IN ('in_stock', 'preorder', 'made_to_order', 'out_of_stock')",
            name="ck_product_external_availability_status",
        ),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_product_external_availability_product_id",
        "product_external_availability",
        ["product_id"],
    )

    op.create_table(
        "product_feedback",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("product_id", sa.Integer(), nullable=False),
        sa.Column("customer_id", sa.Integer(), nullable=False),
        sa.Column("rating", sa.Integer(), nullable=False),
        sa.Column("comment", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="published"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("rating >= 1 AND rating <= 5", name="ck_product_feedback_rating"),
        sa.CheckConstraint("status IN ('published', 'hidden')", name="ck_product_feedback_status"),
        sa.ForeignKeyConstraint(["customer_id"], ["customers.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("product_id", "customer_id", name="uq_product_feedback_customer"),
    )
    op.create_index("ix_product_feedback_product_id", "product_feedback", ["product_id"])
    op.create_index("ix_product_feedback_customer_id", "product_feedback", ["customer_id"])

    op.create_table(
        "showroom_appointments",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("customer_id", sa.Integer(), nullable=False),
        sa.Column("product_id", sa.Integer(), nullable=False),
        sa.Column("starts_at", sa.DateTime(), nullable=False),
        sa.Column("duration_minutes", sa.Integer(), nullable=False, server_default="30"),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="requested"),
        sa.Column("notes", sa.Text(), nullable=False, server_default=""),
        sa.Column("active_slot_key", sa.String(length=32), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "status IN ('requested', 'confirmed', 'cancelled', 'completed')",
            name="ck_showroom_appointments_status",
        ),
        sa.CheckConstraint(
            "duration_minutes >= 15 AND duration_minutes <= 180",
            name="ck_showroom_appointments_duration",
        ),
        sa.ForeignKeyConstraint(["customer_id"], ["customers.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("active_slot_key", name="uq_showroom_appointments_active_slot_key"),
    )
    op.create_index("ix_showroom_appointments_customer_id", "showroom_appointments", ["customer_id"])
    op.create_index("ix_showroom_appointments_product_id", "showroom_appointments", ["product_id"])
    op.create_index("ix_showroom_appointments_starts_at", "showroom_appointments", ["starts_at"])
    op.create_index("ix_showroom_appointments_status", "showroom_appointments", ["status"])


def downgrade() -> None:
    op.drop_index("ix_showroom_appointments_status", table_name="showroom_appointments")
    op.drop_index("ix_showroom_appointments_starts_at", table_name="showroom_appointments")
    op.drop_index("ix_showroom_appointments_product_id", table_name="showroom_appointments")
    op.drop_index("ix_showroom_appointments_customer_id", table_name="showroom_appointments")
    op.drop_table("showroom_appointments")
    op.drop_index("ix_product_feedback_customer_id", table_name="product_feedback")
    op.drop_index("ix_product_feedback_product_id", table_name="product_feedback")
    op.drop_table("product_feedback")
    op.drop_index("ix_product_external_availability_product_id", table_name="product_external_availability")
    op.drop_table("product_external_availability")
    op.drop_index("ix_product_videos_product_id", table_name="product_videos")
    op.drop_table("product_videos")
    op.drop_index("ix_product_merchandising_grid_rank", table_name="product_merchandising")
    op.drop_index("ix_product_merchandising_product_id", table_name="product_merchandising")
    op.drop_table("product_merchandising")
