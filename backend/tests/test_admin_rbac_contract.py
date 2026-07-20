import ast
from pathlib import Path


API_ROOT = Path(__file__).resolve().parents[1] / "api"

EXPECTED_PERMISSIONS = {
    "admin.admin_products": {"products.read"},
    "admin.admin_create_product": {"products.write"},
    "admin.admin_toggle_product": {"products.write"},
    "admin.admin_update_stock": {"inventory.write"},
    "admin.admin_orders": {"orders.read"},
    "admin.admin_update_order": {"orders.write"},
    "admin.admin_create_promo": {"promo.write"},
    "admin.admin_notifications": {"notifications.read"},
    "admin.admin_import_products_csv": {"products.write"},
    "admin.admin_export_orders_csv": {"orders.read"},
    "admin.admin_audit_logs": {"audit.read"},
    "admin.admin_product_detail": {"products.read"},
    "admin.admin_product_update": {"products.write"},
    "admin.admin_customers": {"customers.read"},
    "admin.admin_customer_detail": {"customers.read"},
    "admin.create_mapping_rule": {"moysklad.write"},
    "admin.list_mapping_rules": {"moysklad.read"},
    "admin.list_moysklad_conflicts": {"moysklad.read"},
    "admin_security.login_events": {"admin_security.read"},
    "admin_security.sessions": {"admin_security.read"},
    "admin_security.revoke_sessions": {"admin_security.write"},
    "admin_security.password_reset": {"admin_security.write"},
    "admin_security.set_totp": {"admin_security.write"},
    "admin_security.ip_allowlist": {"admin_security.read"},
    "admin_security.add_ip_rule": {"admin_security.write"},
    "business_analytics.summary": {"analytics.read"},
    "campaigns.create_campaign": {"campaigns.write"},
    "campaigns.queue": {"campaigns.write"},
    "campaigns.list_campaigns": {"campaigns.read"},
    "campaigns.schedule_campaign": {"campaigns.write"},
    "catalog_management.archive_products": {"products.archive"},
    "catalog_management.restore_products": {"products.archive"},
    "catalog_management.bulk_update_prices": {"prices.write"},
    "catalog_management.bulk_update_category": {"products.write"},
    "catalog_management.bulk_update_stock": {"inventory.write"},
    "catalog_management.archived_products": {"products.read"},
    "crm.recompute": {"crm.write"},
    "crm.profiles": {"crm.read"},
    "delivery.create_delivery_zone": {"delivery.write"},
    "delivery_providers.providers": {"delivery.read"},
    "delivery_providers.upsert_provider": {"delivery.write"},
    "delivery_providers.create_order_shipment": {"delivery.write"},
    "delivery_providers.patch_shipment": {"delivery.write"},
    "delivery_providers.shipments": {"delivery.read"},
    "diagnostics.diagnostics": {"diagnostics.read"},
    "enterprise.product_versions": {"pim.read"},
    "enterprise.create_product_version": {"pim.write"},
    "enterprise.product_version_action": {"pim.write", "pim.approve"},
    "enterprise.bulk_edit_products": {"pim.bulk"},
    "enterprise.suppliers": {"suppliers.read"},
    "enterprise.create_supplier": {"suppliers.write"},
    "enterprise.supplier_status": {"suppliers.approve"},
    "enterprise.supplier_documents": {"suppliers.read"},
    "enterprise.create_supplier_document": {"suppliers.write"},
    "enterprise.promotions": {"promotions.read"},
    "enterprise.create_promotion": {"promotions.write"},
    "enterprise.promotion_active": {"promotions.approve"},
    "enterprise.create_workflow": {"workflows.manage"},
    "enterprise.workflow_requests": {"workflows.read"},
    "enterprise.create_workflow_request": {"workflows.submit"},
    "enterprise.workflow_decision": {"workflows.approve"},
    "enterprise.workflow_history": {"workflows.read"},
    "enterprise.feature_flags": {"feature_flags.read"},
    "enterprise.upsert_feature_flag": {"feature_flags.write"},
    "enterprise.asset_metadata": {"dam.write"},
    "enterprise.search_assets": {"dam.read"},
    "fulfillment.list_tasks": {"fulfillment.read"},
    "fulfillment.update_task": {"fulfillment.write"},
    "fulfillment.list_sla": {"fulfillment.read"},
    "fulfillment.task_picklist": {"fulfillment.read"},
    "fulfillment.update_task_item": {"fulfillment.write"},
    "import_export.export_products": {"products.read"},
    "import_export.export_orders": {"orders.read"},
    "looks.create_look": {"products.write"},
    "media.upload_media": {"media.write"},
    "moysklad.sync_moysklad": {"moysklad.sync"},
    "moysklad.sync_logs": {"moysklad.read"},
    "moysklad_deep_mapping.sku_matches": {"moysklad.read"},
    "moysklad_deep_mapping.confirm": {"moysklad.write"},
    "ops.abandoned_carts": {"operations.read"},
    "ops.queue_abandoned_cart_notifications": {"operations.write"},
    "ops.low_stock": {"inventory.read"},
    "ops.inventory_snapshot": {"inventory.write"},
    "outbox.list_outbox": {"webhooks.read"},
    "outbox.retry_outbox": {"webhooks.write"},
    "payment_reconciliation.list_reconciliation": {"payments.reconcile.read"},
    "payment_reconciliation.check_payment": {"payments.reconcile.write"},
    "payment_reconciliation.resolve": {"payments.reconcile.write"},
    "platform.upsert_feature": {"feature_flags.write"},
    "platform.upsert_remote_config": {"remote_config.write"},
    "platform.upsert_page": {"cms.write"},
    "platform.create_block": {"cms.write"},
    "platform.list_events": {"events.read"},
    "platform.list_audit_trail": {"audit.read"},
    "privacy.admin_privacy_requests": {"privacy.read"},
    "privacy.admin_process_privacy_request": {"privacy.write"},
    "recommendations.rebuild": {"products.write"},
    "recommendations.rebuild_v2": {"products.write"},
    "reconciliation.stock_reconciliation_logs": {"inventory.read"},
    "returns.approve_return": {"refunds.write"},
    "search.rebuild": {"products.write"},
    "search.configure_meili": {"products.write"},
    "support.admin_tickets": {"support.read"},
    "support.admin_update_ticket": {"support.write"},
    "timeline.admin_customer_timeline": {"customers.read"},
    "webhook_destinations.list_destinations": {"webhooks.read"},
    "webhook_destinations.create_destination": {"webhooks.write"},
}


def _is_route(function):
    return any(
        isinstance(decorator, ast.Call)
        and isinstance(decorator.func, ast.Attribute)
        and decorator.func.attr in {"get", "post", "put", "patch", "delete"}
        for decorator in function.decorator_list
    )


def _depends_on_current_admin(function):
    return any(
        isinstance(node, ast.Name) and node.id == "get_current_admin"
        for node in ast.walk(function)
    )


def _resolved_permission_values(function):
    assignments = {}
    for node in ast.walk(function):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    assignments[target.id] = {
                        value.value
                        for value in ast.walk(node.value)
                        if isinstance(value, ast.Constant)
                        and isinstance(value.value, str)
                        and "." in value.value
                    }

    permissions = set()
    for node in ast.walk(function):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "require_permission"
        ):
            continue
        permission = node.args[2]
        if isinstance(permission, ast.Constant):
            permissions.add(permission.value)
        elif isinstance(permission, ast.Name):
            permissions.update(assignments.get(permission.id, set()))
    return permissions


def test_every_admin_endpoint_has_exact_least_privilege_permission():
    actual = {}
    for path in sorted(API_ROOT.rglob("*.py")):
        module = path.relative_to(API_ROOT).with_suffix("").as_posix().replace("/", ".")
        tree = ast.parse(path.read_text())
        for node in tree.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if _is_route(node) and _depends_on_current_admin(node):
                actual[f"{module}.{node.name}"] = _resolved_permission_values(node)

    assert actual == EXPECTED_PERMISSIONS


def test_sensitive_permissions_are_not_granted_to_unrelated_default_roles():
    from backend.services.rbac import DEFAULT_PERMISSIONS

    assert "admin_security.write" not in DEFAULT_PERMISSIONS["admin"]
    assert "refunds.write" not in DEFAULT_PERMISSIONS["support"]
    assert "privacy.write" not in DEFAULT_PERMISSIONS["support"]
    assert "campaigns.write" not in DEFAULT_PERMISSIONS["support"]
    assert "campaigns.write" not in DEFAULT_PERMISSIONS["marketing"]
    assert "delivery.write" not in DEFAULT_PERMISSIONS["marketing"]
    assert "delivery.write" not in DEFAULT_PERMISSIONS["warehouse"]
    assert "fulfillment.write" not in DEFAULT_PERMISSIONS["warehouse"]
