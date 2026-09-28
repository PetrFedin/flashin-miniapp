#!/usr/bin/env python3
"""Prove the real privacy export route against migrated PostgreSQL.

The probe persists fixed-precision NUMERIC values, calls the actual FastAPI
route with the normal request-scoped database dependency, and overrides only
the customer-auth dependency with a synthetic customer identity. No production
PII or credentials are used.
"""

from __future__ import annotations

from decimal import Decimal
import json
import sys
from pathlib import Path
from uuid import uuid4

from fastapi import Depends
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.database import SessionLocal, engine, get_db
from backend.main import app
from backend.models import (
    Cart,
    ConsentRecord,
    CrmProfile,
    Customer,
    LoyaltyTransaction,
    Order,
    PrivacyRequest,
    Product,
    ProductVariant,
    SupportTicket,
)
from backend.security import get_current_customer
from backend.services.privacy_export import PRIVACY_EXPORT_SCHEMA_VERSION


def _require_postgresql() -> None:
    if engine.dialect.name != "postgresql":
        raise RuntimeError("privacy export smoke requires PostgreSQL")


def _seed() -> dict[str, int | str]:
    token = uuid4().hex
    db = SessionLocal()
    try:
        subject = Customer(
            telegram_id=f"privacy-smoke-{token}",
            username="privacy-smoke",
            first_name="Пётр 🚀",
            last_name="Тест",
            email="privacy@example.test",
        )
        other = Customer(
            telegram_id=f"privacy-other-{token}",
            username="privacy-other",
            first_name="Другой",
            last_name="Клиент",
        )
        product = Product(
            sku=f"PRIVACY-{token}",
            title="Privacy Smoke Product",
            slug=f"privacy-{token}",
            price=Decimal("1234.56"),
        )
        db.add_all([subject, other, product])
        db.flush()
        variant = ProductVariant(
            product_id=product.id,
            size="M",
            sku=f"PRIVACY-V-{token}",
            stock_qty=10,
            reserved_qty=0,
        )
        db.add(variant)
        db.flush()

        order = Order(
            customer_id=subject.id,
            status="paid",
            payment_status="paid",
            delivery_status="not_started",
            total_amount=Decimal("1234.56"),
            delivery_price=Decimal("0.00"),
            discount_amount=Decimal("0.00"),
            loyalty_points_redeemed=Decimal("0.1250"),
            loyalty_discount_amount=Decimal("0.00"),
            currency="RUB",
            delivery_type="pickup",
            address="Тестовый адрес",
            comment="Unicode 🚀",
        )
        db.add(order)
        db.flush()
        from backend.models import CartItem, OrderItem, Payment

        db.add(
            OrderItem(
                order_id=order.id,
                product_id=product.id,
                variant_id=variant.id,
                title="Privacy Smoke Product",
                size="M",
                quantity=1,
                price=Decimal("1234.56"),
            )
        )
        db.add(
            Payment(
                order_id=order.id,
                provider="yookassa",
                provider_payment_id=f"privacy-payment-{token}",
                status="succeeded",
                amount=Decimal("1234.56"),
            )
        )

        cart = Cart(
            customer_id=subject.id,
            status="converted",
            loyalty_points_to_redeem=Decimal("12.3400"),
        )
        db.add(cart)
        db.flush()
        db.add(
            CartItem(
                cart_id=cart.id,
                product_id=product.id,
                variant_id=variant.id,
                quantity=1,
            )
        )
        db.add_all(
            [
                CrmProfile(
                    customer_id=subject.id,
                    segment="active",
                    orders_count=1,
                    total_spent=Decimal("1234.56"),
                    average_order_value=Decimal("1234.56"),
                    loyalty_points=Decimal("42.1250"),
                    vip=False,
                ),
                LoyaltyTransaction(
                    customer_id=subject.id,
                    order_id=order.id,
                    points_delta=Decimal("1.2500"),
                    reason="order_paid",
                ),
                ConsentRecord(
                    customer_id=subject.id,
                    consent_type="privacy",
                    granted=True,
                    source="privacy-smoke",
                ),
                PrivacyRequest(
                    customer_id=subject.id,
                    request_type="export",
                    status="requested",
                ),
                SupportTicket(
                    customer_id=subject.id,
                    order_id=order.id,
                    subject="Экспорт",
                    message="Проверка Unicode 🚀",
                    status="open",
                    priority="normal",
                ),
                SupportTicket(
                    customer_id=other.id,
                    subject="OTHER",
                    message=f"OTHER-CUSTOMER-SECRET-{token}",
                    status="open",
                    priority="normal",
                ),
            ]
        )
        db.commit()
        return {
            "subject_id": int(subject.id),
            "other_id": int(other.id),
            "product_id": int(product.id),
            "variant_id": int(variant.id),
            "order_id": int(order.id),
            "cart_id": int(cart.id),
            "other_marker": f"OTHER-CUSTOMER-SECRET-{token}",
        }
    finally:
        db.close()


def _cleanup(ids: dict[str, int | str]) -> None:
    from backend.models import CartItem, OrderItem, Payment

    db = SessionLocal()
    try:
        subject_id = int(ids["subject_id"])
        other_id = int(ids["other_id"])
        order_id = int(ids["order_id"])
        cart_id = int(ids["cart_id"])
        variant_id = int(ids["variant_id"])
        product_id = int(ids["product_id"])

        db.query(SupportTicket).filter(
            SupportTicket.customer_id.in_([subject_id, other_id])
        ).delete(synchronize_session=False)
        db.query(PrivacyRequest).filter(
            PrivacyRequest.customer_id == subject_id
        ).delete(synchronize_session=False)
        db.query(ConsentRecord).filter(
            ConsentRecord.customer_id == subject_id
        ).delete(synchronize_session=False)
        db.query(LoyaltyTransaction).filter(
            LoyaltyTransaction.customer_id == subject_id
        ).delete(synchronize_session=False)
        db.query(CrmProfile).filter(
            CrmProfile.customer_id == subject_id
        ).delete(synchronize_session=False)
        db.query(Payment).filter(Payment.order_id == order_id).delete(
            synchronize_session=False
        )
        db.query(OrderItem).filter(OrderItem.order_id == order_id).delete(
            synchronize_session=False
        )
        db.query(Order).filter(Order.id == order_id).delete(
            synchronize_session=False
        )
        db.query(CartItem).filter(CartItem.cart_id == cart_id).delete(
            synchronize_session=False
        )
        db.query(Cart).filter(Cart.id == cart_id).delete(
            synchronize_session=False
        )
        db.query(ProductVariant).filter(ProductVariant.id == variant_id).delete(
            synchronize_session=False
        )
        db.query(Product).filter(Product.id == product_id).delete(
            synchronize_session=False
        )
        db.query(Customer).filter(Customer.id.in_([subject_id, other_id])).delete(
            synchronize_session=False
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def main() -> int:
    _require_postgresql()
    ids = _seed()

    def current_customer_override(
        db: Session = Depends(get_db),
    ) -> Customer:
        customer = db.get(Customer, int(ids["subject_id"]))
        if customer is None:
            raise RuntimeError("privacy smoke subject is missing")
        return customer

    app.dependency_overrides[get_current_customer] = current_customer_override
    try:
        with TestClient(app) as client:
            response = client.get("/api/privacy/export")
        assert response.status_code == 200, response.text
        assert response.headers["content-type"].startswith("application/json")
        disposition = response.headers.get("content-disposition", "")
        assert "attachment;" in disposition
        assert 'filename="flashin_customer_export_v1.json"' in disposition
        assert response.headers.get("cache-control") == "no-store, max-age=0"
        assert int(response.headers["content-length"]) == len(response.content)

        raw = response.content.decode("utf-8")
        payload = json.loads(raw)
        assert payload["schema_version"] == PRIVACY_EXPORT_SCHEMA_VERSION
        assert payload["customer"]["first_name"] == "Пётр 🚀"
        assert payload["orders"][0]["total_amount"] == "1234.56"
        assert payload["orders"][0]["loyalty_points_redeemed"] == "0.1250"
        assert payload["orders"][0]["items"][0]["price"] == "1234.56"
        assert payload["orders"][0]["payments"][0]["amount"] == "1234.56"
        assert payload["carts"][0]["loyalty_points_to_redeem"] == "12.3400"
        assert payload["loyalty"]["profile"]["loyalty_points"] == "42.1250"
        assert payload["loyalty"]["transactions"][0]["points_delta"] == "1.2500"
        assert payload["privacy_requests"][0]["processed_at"] is None
        assert payload["support_tickets"][0]["message"] == "Проверка Unicode 🚀"
        assert str(ids["other_marker"]) not in raw

        print(
            {
                "status": "ok",
                "route": "/api/privacy/export",
                "postgresql": True,
                "schema_version": payload["schema_version"],
                "decimal_exact": True,
                "unicode": True,
                "ownership_isolated": True,
                "content_length": len(response.content),
            }
        )
        return 0
    finally:
        app.dependency_overrides.pop(get_current_customer, None)
        _cleanup(ids)


if __name__ == "__main__":
    raise SystemExit(main())
