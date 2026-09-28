"""Media upload post-commit identity authority.

Revision ID: 0044_media_upload_commit_authority
Revises: 0043_moysklad_variant_identity_authority
Create Date: 2026-09-20
"""

from alembic import op
import sqlalchemy as sa


revision = "0044_media_upload_commit_authority"
down_revision = "0043_moysklad_variant_identity_authority"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "media_assets",
        sa.Column("upload_key", sa.String(length=255), nullable=False, server_default=""),
    )
    predicate = sa.text("upload_key <> ''")
    op.create_index(
        "uq_media_assets_upload_key_nonempty",
        "media_assets",
        ["upload_key"],
        unique=True,
        postgresql_where=predicate,
        sqlite_where=predicate,
    )
    op.alter_column("media_assets", "upload_key", server_default=None)


def downgrade() -> None:
    op.drop_index("uq_media_assets_upload_key_nonempty", table_name="media_assets")
    op.drop_column("media_assets", "upload_key")
