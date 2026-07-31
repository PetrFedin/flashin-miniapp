"""enforce media asset and derivative integrity

Revision ID: 0031_media_asset_integrity
Revises: 0030_privacy_request_integrity
Create Date: 2026-07-31
"""

from alembic import op
import sqlalchemy as sa

revision = "0031_media_asset_integrity"
down_revision = "0030_privacy_request_integrity"
branch_labels = None
depends_on = None

_MAX_MEDIA_BYTES = 10 * 1024 * 1024
_MAX_DIMENSION = 12_000


def upgrade():
    # Normalize and quarantine malformed legacy metadata before enabling strict
    # constraints. Quarantined keys are deterministic and cannot alias a live
    # object uploaded by the application.
    op.execute(
        sa.text(
            f"""
            UPDATE media_assets
            SET storage_key = CASE
                    WHEN trim(coalesce(storage_key, '')) ~ '^[A-Za-z0-9][A-Za-z0-9._-]{{0,254}}$'
                        THEN trim(storage_key)
                    ELSE 'legacy-asset-' || id::text || '.jpg'
                END,
                filename = CASE
                    WHEN trim(coalesce(filename, '')) = ''
                        THEN 'legacy-asset-' || id::text || '.jpg'
                    ELSE left(trim(replace(filename, E'\\\\', '/')), 255)
                END,
                content_type = CASE
                    WHEN lower(trim(coalesce(content_type, ''))) IN (
                        'image/jpeg', 'image/png', 'image/webp'
                    ) THEN lower(trim(content_type))
                    ELSE 'image/jpeg'
                END,
                size_bytes = least(greatest(coalesce(size_bytes, 0), 1), {_MAX_MEDIA_BYTES}),
                url = CASE
                    WHEN trim(coalesce(url, '')) = ''
                        THEN '/media/legacy-asset-' || id::text || '.jpg'
                    ELSE left(trim(url), 2048)
                END
            """
        )
    )
    op.execute(
        sa.text(
            """
            WITH ranked AS (
                SELECT id,
                       row_number() OVER (
                           PARTITION BY storage_key
                           ORDER BY id
                       ) AS rn
                FROM media_assets
            )
            UPDATE media_assets a
            SET storage_key = 'legacy-asset-duplicate-' || a.id::text || '.jpg',
                filename = 'legacy-asset-duplicate-' || a.id::text || '.jpg',
                url = '/media/legacy-asset-duplicate-' || a.id::text || '.jpg',
                content_type = 'image/jpeg'
            FROM ranked r
            WHERE a.id = r.id AND r.rn > 1
            """
        )
    )

    op.execute(
        sa.text(
            f"""
            UPDATE media_derivatives
            SET derivative_type = lower(trim(coalesce(derivative_type, ''))),
                storage_key = CASE
                    WHEN trim(coalesce(storage_key, '')) ~ '^[A-Za-z0-9][A-Za-z0-9._-]{{0,254}}$'
                        THEN trim(storage_key)
                    ELSE 'legacy-derivative-' || id::text || '.webp'
                END,
                url = CASE
                    WHEN trim(coalesce(url, '')) = ''
                        THEN '/media/legacy-derivative-' || id::text || '.webp'
                    ELSE left(trim(url), 2048)
                END,
                content_type = 'image/webp',
                width = least(greatest(coalesce(width, 0), 1), {_MAX_DIMENSION}),
                height = least(greatest(coalesce(height, 0), 1), {_MAX_DIMENSION}),
                size_bytes = least(greatest(coalesce(size_bytes, 0), 1), {_MAX_MEDIA_BYTES})
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE media_derivatives
            SET derivative_type = 'legacy_duplicate_' || id::text
            WHERE derivative_type NOT IN ('thumbnail', 'webp')
            """
        )
    )
    op.execute(
        sa.text(
            """
            WITH ranked AS (
                SELECT id,
                       row_number() OVER (
                           PARTITION BY media_asset_id, derivative_type
                           ORDER BY id
                       ) AS rn
                FROM media_derivatives
                WHERE derivative_type IN ('thumbnail', 'webp')
            )
            UPDATE media_derivatives d
            SET derivative_type = 'legacy_duplicate_' || d.id::text
            FROM ranked r
            WHERE d.id = r.id AND r.rn > 1
            """
        )
    )
    op.execute(
        sa.text(
            """
            WITH ranked AS (
                SELECT id,
                       row_number() OVER (
                           PARTITION BY storage_key
                           ORDER BY id
                       ) AS rn
                FROM media_derivatives
            )
            UPDATE media_derivatives d
            SET storage_key = 'legacy-derivative-duplicate-' || d.id::text || '.webp',
                url = '/media/legacy-derivative-duplicate-' || d.id::text || '.webp'
            FROM ranked r
            WHERE d.id = r.id AND r.rn > 1
            """
        )
    )

    op.create_check_constraint(
        "ck_media_assets_url_normalized",
        "media_assets",
        "url = trim(url)",
    )
    op.create_check_constraint(
        "ck_media_assets_url_size",
        "media_assets",
        "length(url) BETWEEN 1 AND 2048",
    )
    op.create_check_constraint(
        "ck_media_assets_storage_key_normalized",
        "media_assets",
        "storage_key = trim(storage_key)",
    )
    op.create_check_constraint(
        "ck_media_assets_storage_key_size",
        "media_assets",
        "length(storage_key) BETWEEN 1 AND 255",
    )
    op.create_check_constraint(
        "ck_media_assets_filename_normalized",
        "media_assets",
        "filename = trim(filename)",
    )
    op.create_check_constraint(
        "ck_media_assets_filename_size",
        "media_assets",
        "length(filename) BETWEEN 1 AND 255",
    )
    op.create_check_constraint(
        "ck_media_assets_content_type_valid",
        "media_assets",
        "content_type IN ('image/jpeg', 'image/png', 'image/webp')",
    )
    op.create_check_constraint(
        "ck_media_assets_size_range",
        "media_assets",
        f"size_bytes BETWEEN 1 AND {_MAX_MEDIA_BYTES}",
    )
    op.create_index(
        "uq_media_assets_storage_key",
        "media_assets",
        ["storage_key"],
        unique=True,
    )

    op.create_check_constraint(
        "ck_media_derivatives_type_valid",
        "media_derivatives",
        "derivative_type IN ('thumbnail', 'webp') "
        "OR derivative_type LIKE 'legacy_duplicate_%'",
    )
    op.create_check_constraint(
        "ck_media_derivatives_type_normalized",
        "media_derivatives",
        "derivative_type = lower(trim(derivative_type))",
    )
    op.create_check_constraint(
        "ck_media_derivatives_url_normalized",
        "media_derivatives",
        "url = trim(url)",
    )
    op.create_check_constraint(
        "ck_media_derivatives_url_size",
        "media_derivatives",
        "length(url) BETWEEN 1 AND 2048",
    )
    op.create_check_constraint(
        "ck_media_derivatives_storage_key_normalized",
        "media_derivatives",
        "storage_key = trim(storage_key)",
    )
    op.create_check_constraint(
        "ck_media_derivatives_storage_key_size",
        "media_derivatives",
        "length(storage_key) BETWEEN 1 AND 255",
    )
    op.create_check_constraint(
        "ck_media_derivatives_content_type_valid",
        "media_derivatives",
        "content_type = 'image/webp'",
    )
    op.create_check_constraint(
        "ck_media_derivatives_dimensions_range",
        "media_derivatives",
        f"width BETWEEN 1 AND {_MAX_DIMENSION} AND height BETWEEN 1 AND {_MAX_DIMENSION}",
    )
    op.create_check_constraint(
        "ck_media_derivatives_size_range",
        "media_derivatives",
        f"size_bytes BETWEEN 1 AND {_MAX_MEDIA_BYTES}",
    )
    op.create_index(
        "uq_media_derivatives_storage_key",
        "media_derivatives",
        ["storage_key"],
        unique=True,
    )
    op.create_index(
        "uq_media_derivatives_asset_type",
        "media_derivatives",
        ["media_asset_id", "derivative_type"],
        unique=True,
        postgresql_where=sa.text("derivative_type IN ('thumbnail', 'webp')"),
    )


def downgrade():
    op.drop_index("uq_media_derivatives_asset_type", table_name="media_derivatives")
    op.drop_index("uq_media_derivatives_storage_key", table_name="media_derivatives")
    for name in (
        "ck_media_derivatives_size_range",
        "ck_media_derivatives_dimensions_range",
        "ck_media_derivatives_content_type_valid",
        "ck_media_derivatives_storage_key_size",
        "ck_media_derivatives_storage_key_normalized",
        "ck_media_derivatives_url_size",
        "ck_media_derivatives_url_normalized",
        "ck_media_derivatives_type_normalized",
        "ck_media_derivatives_type_valid",
    ):
        op.drop_constraint(name, "media_derivatives", type_="check")

    op.drop_index("uq_media_assets_storage_key", table_name="media_assets")
    for name in (
        "ck_media_assets_size_range",
        "ck_media_assets_content_type_valid",
        "ck_media_assets_filename_size",
        "ck_media_assets_filename_normalized",
        "ck_media_assets_storage_key_size",
        "ck_media_assets_storage_key_normalized",
        "ck_media_assets_url_size",
        "ck_media_assets_url_normalized",
    ):
        op.drop_constraint(name, "media_assets", type_="check")
