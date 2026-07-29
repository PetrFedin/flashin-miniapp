#!/usr/bin/env python3
"""Read-only database audit for transactional constraints through revision 0011."""

import argparse
import json
import sys
from collections.abc import Mapping

from sqlalchemy import inspect, text
from sqlalchemy.exc import SQLAlchemyError

from backend.database import engine

CHECKS: Mapping[str, str] = {
    "invalid_variant_inventory": """
        SELECT count(*) FROM product_variants
        WHERE stock_qty < 0 OR reserved_qty < 0 OR reserved_qty > stock_qty
    """,
    "invalid_cart_item_quantity": """
        SELECT count(*) FROM cart_items WHERE quantity <= 0 OR quantity > 10
    """,
    "invalid_order_items": """
        SELECT count(*) FROM order_items WHERE quantity <= 0 OR price < 0
    """,
    "negative_order_financials": """
        SELECT count(*) FROM orders
        WHERE total_amount < 0
           OR delivery_price < 0
           OR discount_amount < 0
           OR loyalty_points_redeemed < 0
           OR loyalty_discount_amount < 0
    """,
    "invalid_promo_counters": """
        SELECT count(*) FROM promo_codes
        WHERE discount_value < 0
           OR min_amount < 0
           OR max_uses < 0
           OR used_count < 0
           OR (max_uses > 0 AND used_count > max_uses)
    """,
    "non_positive_payments": "SELECT count(*) FROM payments WHERE amount <= 0",
    "negative_refund_amounts": "SELECT count(*) FROM return_requests WHERE refund_amount < 0",
    "negative_loyalty_balances": "SELECT count(*) FROM crm_profiles WHERE loyalty_points < 0",
    "non_positive_loyalty_holds": "SELECT count(*) FROM loyalty_redemption_holds WHERE points <= 0",
    "duplicate_active_carts": """
        SELECT count(*) FROM (
            SELECT customer_id FROM carts
            WHERE status = 'active'
            GROUP BY customer_id HAVING count(*) > 1
        ) conflicts
    """,
    "duplicate_provider_payments": """
        SELECT count(*) FROM (
            SELECT provider, provider_payment_id FROM payments
            WHERE provider_payment_id <> ''
            GROUP BY provider, provider_payment_id HAVING count(*) > 1
        ) conflicts
    """,
    "duplicate_payment_events": """
        SELECT count(*) FROM (
            SELECT provider, provider_payment_id, event_type FROM payment_events
            WHERE provider_payment_id <> '' AND event_type <> ''
            GROUP BY provider, provider_payment_id, event_type HAVING count(*) > 1
        ) conflicts
    """,
    "duplicate_order_returns": """
        SELECT count(*) FROM (
            SELECT order_id FROM return_requests
            GROUP BY order_id HAVING count(*) > 1
        ) conflicts
    """,
    "duplicate_provider_refunds": """
        SELECT count(*) FROM (
            SELECT provider_refund_id FROM return_requests
            WHERE provider_refund_id <> ''
            GROUP BY provider_refund_id HAVING count(*) > 1
        ) conflicts
    """,
    "duplicate_admin_permissions": """
        SELECT count(*) FROM (
            SELECT role, permission FROM admin_role_permissions
            GROUP BY role, permission HAVING count(*) > 1
        ) conflicts
    """,
    "duplicate_fulfillment_tasks": """
        SELECT count(*) FROM (
            SELECT order_id FROM fulfillment_tasks
            GROUP BY order_id HAVING count(*) > 1
        ) conflicts
    """,
    "duplicate_admin_sessions": """
        SELECT count(*) FROM (
            SELECT session_token_hash FROM admin_sessions
            GROUP BY session_token_hash HAVING count(*) > 1
        ) conflicts
    """,
    "duplicate_password_reset_tokens": """
        SELECT count(*) FROM (
            SELECT token_hash FROM admin_password_resets
            GROUP BY token_hash HAVING count(*) > 1
        ) conflicts
    """,
    "duplicate_order_loyalty_transactions": """
        SELECT count(*) FROM (
            SELECT customer_id, order_id, reason FROM loyalty_transactions
            WHERE order_id IS NOT NULL
              AND reason IN (
                  'order_paid',
                  'loyalty_redeemed',
                  'referral_reward',
                  'loyalty_refund',
                  'order_refund_reversal',
                  'referral_refund_reversal'
              )
            GROUP BY customer_id, order_id, reason HAVING count(*) > 1
        ) conflicts
    """,
    "duplicate_reserved_loyalty_holds": """
        SELECT count(*) FROM (
            SELECT customer_id, cart_id FROM loyalty_redemption_holds
            WHERE cart_id IS NOT NULL AND status = 'reserved'
            GROUP BY customer_id, cart_id HAVING count(*) > 1
        ) conflicts
    """,
    "duplicate_order_loyalty_holds": """
        SELECT count(*) FROM (
            SELECT customer_id, order_id FROM loyalty_redemption_holds
            WHERE order_id IS NOT NULL AND status IN ('committed', 'refunded')
            GROUP BY customer_id, order_id HAVING count(*) > 1
        ) conflicts
    """,
    "duplicate_active_referral_codes": """
        SELECT count(*) FROM (
            SELECT customer_id FROM referral_codes
            WHERE active = true
            GROUP BY customer_id HAVING count(*) > 1
        ) conflicts
    """,
}

REQUIRED_TABLES = {
    "admin_password_resets",
    "admin_role_permissions",
    "admin_sessions",
    "cart_items",
    "carts",
    "crm_profiles",
    "fulfillment_tasks",
    "loyalty_redemption_holds",
    "loyalty_transactions",
    "order_items",
    "orders",
    "payment_events",
    "payments",
    "product_variants",
    "promo_codes",
    "referral_codes",
    "return_requests",
}


class MissingSchemaError(RuntimeError):
    def __init__(self, missing_tables: set[str]):
        self.missing_tables = missing_tables
        super().__init__("missing tables: " + ", ".join(sorted(missing_tables)))


def run_audit() -> dict[str, int]:
    present_tables = set(inspect(engine).get_table_names())
    missing_tables = REQUIRED_TABLES - present_tables
    if missing_tables:
        raise MissingSchemaError(missing_tables)

    results: dict[str, int] = {}
    with engine.connect() as connection:
        for name, query in CHECKS.items():
            results[name] = int(connection.execute(text(query)).scalar_one())
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    parser.add_argument(
        "--allow-missing-schema",
        action="store_true",
        help="Return success when this is a first deploy and application tables do not exist yet",
    )
    args = parser.parse_args()

    try:
        results = run_audit()
    except MissingSchemaError as exc:
        payload = {
            "ok": args.allow_missing_schema,
            "skipped": args.allow_missing_schema,
            "reason": "missing_schema",
            "missing_tables": sorted(exc.missing_tables),
        }
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        elif args.allow_missing_schema:
            print("Transaction integrity audit skipped: first deploy schema is not present yet")
        else:
            print(f"Transaction integrity audit failed: {exc}", file=sys.stderr)
        return 0 if args.allow_missing_schema else 2
    except SQLAlchemyError as exc:
        payload = {"ok": False, "error": str(exc.__class__.__name__)}
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        else:
            print(f"Transaction integrity audit failed: {exc.__class__.__name__}", file=sys.stderr)
        return 2

    violations = {name: count for name, count in results.items() if count > 0}
    payload = {"ok": not violations, "checks": results, "violations": violations}
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    elif violations:
        print("Transaction integrity audit found conflicts:")
        for name, count in violations.items():
            print(f" - {name}: {count}")
    else:
        print(f"Transaction integrity audit OK ({len(results)} checks)")

    return 1 if violations else 0


if __name__ == "__main__":
    raise SystemExit(main())
