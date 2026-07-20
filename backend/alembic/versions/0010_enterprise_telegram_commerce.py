"""enterprise and Telegram commerce tables

Revision ID: 0010_enterprise_telegram_commerce
Revises: 0009_security_payment_delivery_media_hardening
Create Date: 2026-07-20
"""

from alembic import op
import sqlalchemy as sa


revision = "0010_enterprise_telegram_commerce"
down_revision = "0009_security_payment_delivery_media_hardening"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "product_versions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("product_id", sa.Integer(), sa.ForeignKey("products.id"), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("status", sa.String(32), nullable=False, server_default="draft"),
        sa.Column("snapshot_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("change_note", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("admin_users.id"), nullable=True),
        sa.Column("reviewed_by", sa.Integer(), sa.ForeignKey("admin_users.id"), nullable=True),
        sa.Column("published_at", sa.DateTime(), nullable=True),
        sa.Column("archived_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_product_versions_product_id", "product_versions", ["product_id"])
    op.create_index("ix_product_versions_status", "product_versions", ["status"])

    op.create_table(
        "bulk_edit_jobs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("entity_type", sa.String(64), nullable=False, server_default="product"),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("operation", sa.String(64), nullable=False),
        sa.Column("filter_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("changes_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("total_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("processed_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failed_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("admin_users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_bulk_edit_jobs_entity_type", "bulk_edit_jobs", ["entity_type"])
    op.create_index("ix_bulk_edit_jobs_status", "bulk_edit_jobs", ["status"])
    op.create_index("ix_bulk_edit_jobs_operation", "bulk_edit_jobs", ["operation"])

    op.create_table(
        "suppliers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("legal_name", sa.String(255), nullable=False, server_default=""),
        sa.Column("tax_id", sa.String(64), nullable=False, server_default=""),
        sa.Column("contact_name", sa.String(255), nullable=False, server_default=""),
        sa.Column("email", sa.String(255), nullable=False, server_default=""),
        sa.Column("phone", sa.String(64), nullable=False, server_default=""),
        sa.Column("status", sa.String(32), nullable=False, server_default="draft"),
        sa.Column("notes", sa.Text(), nullable=False, server_default=""),
        sa.Column("metadata_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_suppliers_name", "suppliers", ["name"])
    op.create_index("ix_suppliers_tax_id", "suppliers", ["tax_id"])
    op.create_index("ix_suppliers_email", "suppliers", ["email"])
    op.create_index("ix_suppliers_status", "suppliers", ["status"])

    op.create_table(
        "supplier_documents",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("supplier_id", sa.Integer(), sa.ForeignKey("suppliers.id"), nullable=False),
        sa.Column("document_type", sa.String(64), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("media_asset_id", sa.Integer(), sa.ForeignKey("media_assets.id"), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="uploaded"),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.Column("comment", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_supplier_documents_supplier_id", "supplier_documents", ["supplier_id"])
    op.create_index("ix_supplier_documents_document_type", "supplier_documents", ["document_type"])
    op.create_index("ix_supplier_documents_status", "supplier_documents", ["status"])

    op.create_table(
        "promotion_rules",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("promotion_type", sa.String(64), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("stackable", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("starts_at", sa.DateTime(), nullable=True),
        sa.Column("ends_at", sa.DateTime(), nullable=True),
        sa.Column("conditions_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("actions_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("usage_limit", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("usage_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("budget_limit", sa.Float(), nullable=False, server_default="0"),
        sa.Column("spent_amount", sa.Float(), nullable=False, server_default="0"),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("admin_users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_promotion_rules_name", "promotion_rules", ["name"])
    op.create_index("ix_promotion_rules_code", "promotion_rules", ["code"], unique=True)
    op.create_index("ix_promotion_rules_promotion_type", "promotion_rules", ["promotion_type"])
    op.create_index("ix_promotion_rules_active", "promotion_rules", ["active"])

    op.create_table(
        "workflow_definitions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("entity_type", sa.String(64), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("steps_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_workflow_definitions_name", "workflow_definitions", ["name"])
    op.create_index("ix_workflow_definitions_entity_type", "workflow_definitions", ["entity_type"])

    op.create_table(
        "workflow_requests",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("workflow_id", sa.Integer(), sa.ForeignKey("workflow_definitions.id"), nullable=False),
        sa.Column("entity_type", sa.String(64), nullable=False),
        sa.Column("entity_id", sa.String(128), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("current_step", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("payload_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("requested_by", sa.Integer(), sa.ForeignKey("admin_users.id"), nullable=True),
        sa.Column("assigned_role", sa.String(64), nullable=False, server_default=""),
        sa.Column("comment", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_workflow_requests_workflow_id", "workflow_requests", ["workflow_id"])
    op.create_index("ix_workflow_requests_entity_type", "workflow_requests", ["entity_type"])
    op.create_index("ix_workflow_requests_entity_id", "workflow_requests", ["entity_id"])
    op.create_index("ix_workflow_requests_status", "workflow_requests", ["status"])

    op.create_table(
        "workflow_actions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("request_id", sa.Integer(), sa.ForeignKey("workflow_requests.id"), nullable=False),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column("step_index", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("actor_id", sa.Integer(), sa.ForeignKey("admin_users.id"), nullable=True),
        sa.Column("comment", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_workflow_actions_request_id", "workflow_actions", ["request_id"])
    op.create_index("ix_workflow_actions_action", "workflow_actions", ["action"])

    op.create_table(
        "media_asset_metadata",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("media_asset_id", sa.Integer(), sa.ForeignKey("media_assets.id"), nullable=False),
        sa.Column("alt_text", sa.Text(), nullable=False, server_default=""),
        sa.Column("tags_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("ai_labels_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("dominant_colors_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("width", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("height", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("checksum", sa.String(128), nullable=False, server_default=""),
        sa.Column("derivatives_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("media_asset_id", name="uq_media_asset_metadata_asset"),
    )
    op.create_index("ix_media_asset_metadata_media_asset_id", "media_asset_metadata", ["media_asset_id"])
    op.create_index("ix_media_asset_metadata_checksum", "media_asset_metadata", ["checksum"])

    op.create_table(
        "telegram_offers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code", sa.String(80), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("offer_type", sa.String(40), nullable=False),
        sa.Column("stars_amount", sa.Integer(), nullable=False),
        sa.Column("duration_days", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("certificate_value", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("starts_at", sa.DateTime(), nullable=True),
        sa.Column("ends_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_telegram_offers_code", "telegram_offers", ["code"], unique=True)
    op.create_index("ix_telegram_offers_offer_type", "telegram_offers", ["offer_type"])

    op.create_table(
        "telegram_purchases",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("customer_id", sa.Integer(), sa.ForeignKey("customers.id"), nullable=False),
        sa.Column("offer_id", sa.Integer(), sa.ForeignKey("telegram_offers.id"), nullable=False),
        sa.Column("invoice_payload", sa.String(255), nullable=False),
        sa.Column("invoice_url", sa.String(2048), nullable=False, server_default=""),
        sa.Column("stars_amount", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="invoice_created"),
        sa.Column("recipient_telegram_id", sa.String(64), nullable=False, server_default=""),
        sa.Column("recipient_username", sa.String(255), nullable=False, server_default=""),
        sa.Column("gift_message", sa.Text(), nullable=False, server_default=""),
        sa.Column("telegram_payment_charge_id", sa.String(255), nullable=True),
        sa.Column("provider_payment_charge_id", sa.String(255), nullable=False, server_default=""),
        sa.Column("paid_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_telegram_purchases_customer_id", "telegram_purchases", ["customer_id"])
    op.create_index("ix_telegram_purchases_offer_id", "telegram_purchases", ["offer_id"])
    op.create_index("ix_telegram_purchases_invoice_payload", "telegram_purchases", ["invoice_payload"], unique=True)
    op.create_index("ix_telegram_purchases_status", "telegram_purchases", ["status"])
    op.create_index("ix_telegram_purchases_recipient_telegram_id", "telegram_purchases", ["recipient_telegram_id"])
    op.create_index(
        "ux_telegram_purchases_payment_charge_nonempty",
        "telegram_purchases",
        ["telegram_payment_charge_id"],
        unique=True,
        postgresql_where=sa.text(
            "telegram_payment_charge_id IS NOT NULL "
            "AND telegram_payment_charge_id <> ''"
        ),
        sqlite_where=sa.text(
            "telegram_payment_charge_id IS NOT NULL "
            "AND telegram_payment_charge_id <> ''"
        ),
    )

    op.create_table(
        "gift_certificates",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("purchase_id", sa.Integer(), sa.ForeignKey("telegram_purchases.id"), nullable=False),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("value_rub", sa.Integer(), nullable=False),
        sa.Column("balance_rub", sa.Integer(), nullable=False),
        sa.Column("owner_customer_id", sa.Integer(), sa.ForeignKey("customers.id"), nullable=True),
        sa.Column("recipient_telegram_id", sa.String(64), nullable=False, server_default=""),
        sa.Column("recipient_username", sa.String(255), nullable=False, server_default=""),
        sa.Column("status", sa.String(32), nullable=False, server_default="active"),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.Column("redeemed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_gift_certificates_purchase_id", "gift_certificates", ["purchase_id"], unique=True)
    op.create_index("ix_gift_certificates_code", "gift_certificates", ["code"], unique=True)
    op.create_index("ix_gift_certificates_owner_customer_id", "gift_certificates", ["owner_customer_id"])
    op.create_index("ix_gift_certificates_recipient_telegram_id", "gift_certificates", ["recipient_telegram_id"])
    op.create_index("ix_gift_certificates_status", "gift_certificates", ["status"])

    op.create_table(
        "club_memberships",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("customer_id", sa.Integer(), sa.ForeignKey("customers.id"), nullable=False),
        sa.Column("level", sa.String(32), nullable=False, server_default="club"),
        sa.Column("status", sa.String(32), nullable=False, server_default="active"),
        sa.Column("starts_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("source_purchase_id", sa.Integer(), sa.ForeignKey("telegram_purchases.id"), nullable=False),
        sa.Column("auto_renew", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("customer_id", name="uq_club_membership_customer"),
    )
    op.create_index("ix_club_memberships_customer_id", "club_memberships", ["customer_id"])
    op.create_index("ix_club_memberships_status", "club_memberships", ["status"])
    op.create_index("ix_club_memberships_source_purchase_id", "club_memberships", ["source_purchase_id"])

    op.create_table(
        "telegram_notification_preferences",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("customer_id", sa.Integer(), sa.ForeignKey("customers.id"), nullable=False),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("quiet_hours_start", sa.String(5), nullable=False, server_default=""),
        sa.Column("quiet_hours_end", sa.String(5), nullable=False, server_default=""),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("customer_id", "event_type", name="uq_customer_notification_event"),
    )
    op.create_index("ix_telegram_notification_preferences_customer_id", "telegram_notification_preferences", ["customer_id"])
    op.create_index("ix_telegram_notification_preferences_event_type", "telegram_notification_preferences", ["event_type"])


def downgrade():
    op.drop_table("telegram_notification_preferences")
    op.drop_table("club_memberships")
    op.drop_table("gift_certificates")
    op.drop_table("telegram_purchases")
    op.drop_table("telegram_offers")
    op.drop_table("media_asset_metadata")
    op.drop_table("workflow_actions")
    op.drop_table("workflow_requests")
    op.drop_table("workflow_definitions")
    op.drop_table("promotion_rules")
    op.drop_table("supplier_documents")
    op.drop_table("suppliers")
    op.drop_table("bulk_edit_jobs")
    op.drop_table("product_versions")
