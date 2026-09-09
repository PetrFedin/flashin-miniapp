#!/usr/bin/env python3
"""Adversarial acceptance probes; allowed ONLY on a disposable local database.

Failures intentionally return a nonzero exit status. This is an audit harness,
not a fix and not an alternative to the existing CI, Security or release gates.
No external payment, Telegram or object-storage request is made.
"""
from __future__ import annotations

import ast
import asyncio
import io
import json
import os
import subprocess
import sys
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def safety_guard():
    from sqlalchemy.engine import make_url
    url = make_url(os.environ.get("DATABASE_URL", ""))
    if (os.environ.get("APP_ENV") != "test"
            or os.environ.get("AUDIT_DISPOSABLE_DB") != "true"
            or url.get_backend_name() != "postgresql"
            or url.host != "127.0.0.1"
            or url.database != "flashin_completeness_audit"):
        raise RuntimeError("Audit requires explicit disposable local PostgreSQL database")


def main():
    safety_guard()
    from fastapi import HTTPException, UploadFile
    from fastapi.testclient import TestClient
    from sqlalchemy import text
    from backend.main import app
    from backend.database import SessionLocal, engine, get_db
    from backend.api import media as media_api
    from backend.api import orders as orders_api
    from backend.models import AdminUser, Cart, CartItem, Customer, InventoryMovement, MediaAsset, Product, ProductVariant
    from backend.schemas import CheckoutIn
    from backend.security import get_current_customer
    from backend.services.pricing import load_product_price_quotes

    run_key = uuid.uuid4().hex
    results = []

    def product_fixture(suffix):
        with SessionLocal() as db:
            product = Product(sku=f"AUDIT-{run_key}-{suffix}", slug=f"audit-{run_key}-{suffix}",
                              title="Synthetic audit product", price=Decimal("100.00"), currency="RUB", active=True)
            variant = ProductVariant(product=product, size="M", color="Black", sku=f"AUDIT-V-{run_key}-{suffix}",
                                     stock_qty=1, reserved_qty=0)
            db.add_all([product, variant])
            db.commit()
            return int(product.id), int(variant.id)

    def probe_checkout():
        product_id, variant_id = product_fixture("checkout")
        customer_ids = []
        with SessionLocal() as db:
            for number in range(2):
                customer = Customer(telegram_id=f"audit-{run_key}-{number}", first_name="Audit")
                db.add(customer)
                db.flush()
                cart = Cart(customer_id=customer.id, status="active")
                db.add(cart)
                db.flush()
                db.add(CartItem(cart_id=cart.id, product_id=product_id, variant_id=variant_id, quantity=1))
                customer_ids.append(int(customer.id))
            db.commit()
        barrier = threading.Barrier(2, timeout=15)
        original = orders_api._load_locked_active_cart

        def synchronized_preload(db, customer_id):
            cart = original(db, customer_id)
            barrier.wait()
            return cart

        def buyer(customer_id):
            with SessionLocal() as db:
                db.execute(text("SET LOCAL lock_timeout = '10s'"))
                db.execute(text("SET LOCAL statement_timeout = '20s'"))
                customer = db.query(Customer).filter(Customer.id == customer_id).one()
                payload = CheckoutIn(name="Audit Buyer", phone="+79990000001", delivery_type="pickup", address="", comment="audit")
                try:
                    order = orders_api.checkout(payload=payload, idempotency_key_header=f"audit-{uuid.uuid4().hex}", customer=customer, db=db)
                    return {"outcome": "accepted", "order_id": int(order.id)}
                except HTTPException as exc:
                    db.rollback()
                    return {"outcome": "rejected", "http_status": exc.status_code}

        with patch.object(orders_api, "_load_locked_active_cart", synchronized_preload):
            with ThreadPoolExecutor(max_workers=2) as pool:
                buyers = list(pool.map(buyer, customer_ids))
        with SessionLocal() as db:
            variant = db.query(ProductVariant).filter(ProductVariant.id == variant_id).one()
            movements = db.query(InventoryMovement).filter(InventoryMovement.variant_id == variant_id, InventoryMovement.kind == "reserve").all()
            accepted = sum(row["outcome"] == "accepted" for row in buyers)
            rejected_409 = sum(row.get("http_status") == 409 for row in buyers)
            ledger_quantity = sum(row.quantity for row in movements)
            passed = accepted == 1 and rejected_409 == 1 and variant.reserved_qty == 1 and ledger_quantity == 1
            return {"id": "AC-INV-01", "status": "PASS" if passed else "FAIL",
                    "scope": "Actual checkout handler; two real PostgreSQL Sessions. Scheduling barrier only. Pilot gate disabled as in integrated CI; not a production admission bypass.",
                    "buyers": buyers, "accepted_orders": accepted, "stock_qty": variant.stock_qty,
                    "reserved_qty": variant.reserved_qty, "reservation_ledger_quantity": ledger_quantity}

    def probe_price():
        product_id, _ = product_fixture("price")
        with SessionLocal() as reader:
            cached = reader.query(Product).filter(Product.id == product_id).one()
            with SessionLocal() as writer:
                current = writer.query(Product).filter(Product.id == product_id).one()
                current.price = Decimal("200.00")
                writer.commit()
            quote = load_product_price_quotes(reader, [cached], lock=True)[product_id]
            return {"id": "AC-PRICE-01", "status": "PASS" if quote.effective_price == Decimal("200.00") else "FAIL",
                    "scope": "Actual pricing service; two PostgreSQL Sessions, retained identity-map object",
                    "committed_price": "200.00", "locked_quote_price": str(quote.effective_price)}

    def probe_privacy():
        with SessionLocal() as setup:
            customer = Customer(telegram_id=f"audit-{run_key}-privacy", first_name="Audit")
            setup.add(customer)
            setup.flush()
            cart = Cart(customer_id=customer.id, status="active")
            setup.add(cart)
            setup.commit()
            customer_id = int(customer.id)
            setup.refresh(cart)
            persisted_type = type(cart.loyalty_points_to_redeem).__name__

        def synthetic_customer():
            with SessionLocal() as db:
                return db.query(Customer).filter(Customer.id == customer_id).one()

        previous = dict(app.dependency_overrides)
        app.dependency_overrides[get_current_customer] = synthetic_customer
        try:
            client = TestClient(app, raise_server_exceptions=False)
            try:
                response = client.get("/api/privacy/export")
            finally:
                client.close()
            body_valid = False
            if response.status_code == 200:
                payload = response.json()
                body_valid = isinstance(payload.get("carts"), list) and len(payload["carts"]) == 1
            return {"id": "AC-PRIV-01", "status": "PASS" if response.status_code == 200 and body_valid else "FAIL",
                    "scope": "Actual HTTP route, normal request DB dependency, real PostgreSQL. Synthetic customer auth dependency only; no production PII.",
                    "persisted_points_python_type": persisted_type, "http_status": response.status_code,
                    "export_contains_cart": body_valid}
        finally:
            app.dependency_overrides.clear()
            app.dependency_overrides.update(previous)

    def probe_committed_media():
        storage_key = f"{uuid.uuid4().hex}.png"
        deleted = []
        async def accepted_storage(_file):
            return {"storage_key": storage_key, "url": f"https://media.invalid/{storage_key}",
                    "filename": "audit.png", "content_type": "image/png", "size_bytes": 1}
        with SessionLocal() as db:
            admin = AdminUser(email=f"audit-{run_key}@example.invalid", password_hash="unusable-audit-only", role="owner", active=True)
            db.add(admin)
            db.commit()
            admin_id = int(admin.id)
            admin = db.query(AdminUser).filter(AdminUser.id == admin_id).one()
            original_refresh = db.refresh
            def failed_response_refresh(instance, *args, **kwargs):
                if isinstance(instance, MediaAsset):
                    raise RuntimeError("injected post-commit response refresh failure")
                return original_refresh(instance, *args, **kwargs)
            upload = UploadFile(file=io.BytesIO(b"x"), filename="audit.png")
            observed_error = None
            with patch.object(media_api, "save_media", accepted_storage), patch.object(media_api, "delete_media", lambda key: deleted.append(key)), patch.object(media_api, "generate_local_derivatives", lambda *_: None), patch.object(db, "refresh", failed_response_refresh):
                try:
                    asyncio.run(media_api.upload_media(file=upload, admin=admin, db=db))
                except RuntimeError as exc:
                    if str(exc) != "injected post-commit response refresh failure":
                        raise
                    observed_error = type(exc).__name__
            exists = db.query(MediaAsset).filter(MediaAsset.storage_key == storage_key).first() is not None
            bad_delete = exists and storage_key in deleted
            return {"id": "AC-MEDIA-01", "status": "FAIL" if bad_delete else "PASS",
                    "scope": "Actual upload handler and real DB commit. Storage/derivatives replaced; post-commit refresh failure injected. No object was actually deleted.",
                    "committed_asset_exists": exists, "committed_key_sent_to_delete": storage_key in deleted,
                    "observed_error": observed_error}

    for probe in (probe_checkout, probe_price, probe_privacy, probe_committed_media):
        try:
            results.append(probe())
        except Exception as exc:
            results.append({"id": probe.__name__, "status": "ERROR", "error_type": type(exc).__name__,
                            "meaning": "Probe did not complete; not application pass or reproduced defect"})
    routes = sorted({(route.path, method) for route in app.routes for method in (getattr(route, "methods", None) or []) if method not in {"HEAD", "OPTIONS"}})
    tracked = subprocess.check_output(["git", "ls-files"], cwd=ROOT, text=True).splitlines()
    inventory = {name: sum(path.startswith(name + "/") for path in tracked) for name in ("backend", "frontend", "admin", "bot", "e2e", "scripts", "docs")}
    test_functions = 0
    for path in (ROOT / "backend" / "tests").rglob("test_*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        test_functions += sum(isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_") for node in ast.walk(tree))
    with engine.connect() as connection:
        postgres_version = connection.execute(text("SHOW server_version")).scalar()
    report = {"schema_version": 1, "generated_at": datetime.now(timezone.utc).isoformat(),
              "code_sha": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
              "scope": "Four adversarial probes, NOT exhaustive acceptance or live-provider evidence",
              "postgres_version": postgres_version, "results": results,
              "routes": [{"path": path, "method": method} for path, method in routes],
              "registered_route_method_pairs": len(routes), "tracked_file_counts": inventory,
              "backend_test_function_definitions": test_functions, "counts_are_not_coverage": True,
              "application_acceptance": "NO_GO" if any(row["status"] != "PASS" for row in results) else "NOT_ESTABLISHED_BY_FOUR_PROBES"}
    output = ROOT / "audit-output"
    output.mkdir(exist_ok=True)
    (output / "production-completeness-probes.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items() if key != "routes"}, ensure_ascii=False, indent=2))
    return 1 if any(row["status"] != "PASS" for row in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
