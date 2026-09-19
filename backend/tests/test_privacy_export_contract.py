from datetime import datetime, timedelta
from decimal import Decimal
import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend import model_constraints as _model_constraints  # noqa: F401
from backend.auth_models import CustomerAuthEvent, CustomerSession
from backend.database import Base
from backend.models import (
    Cart,
    CartItem,
    ConsentRecord,
    CrmProfile,
    Customer,
    CustomerTimelineEvent,
    LoyaltyTransaction,
    Notification,
    Order,
    OrderItem,
    Payment,
    PrivacyRequest,
    Product,
    ProductVariant,
    ReferralCode,
    ReturnRequest,
    SupportTicket,
)
from backend.services.privacy_export import (
    PRIVACY_EXPORT_SCHEMA_VERSION,
    PrivacyExportUnavailable,
    _iter_customer_rows,
    build_customer_export,
)


def _db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return engine, sessionmaker(bind=engine)()


def _seed_customer_export(db):
    moment = datetime(2026, 9, 19, 12, 34, 56, 123456)
    customer = Customer(
        telegram_id="privacy-subject",
        username="petr",
        first_name="Пётр 🚀",
        last_name="Фёдин",
        phone="+79990000000",
        email="petr@example.test",
        created_at=moment,
    )
    other = Customer(
        telegram_id="privacy-other",
        username="other",
        first_name="Другой",
        last_name="Клиент",
        phone="+78880000000",
        email="other@example.test",
        created_at=moment,
    )
    product = Product(
        sku="PRIVACY-PRODUCT",
        title="Куртка",
        slug="privacy-product",
        price=Decimal("1234.56"),
        created_at=moment,
        updated_at=moment,
    )
    db.add_all([customer, other, product])
    db.flush()
    variant = ProductVariant(
        product_id=product.id,
        size="M",
        color="Чёрный",
        sku="PRIVACY-VARIANT",
        stock_qty=10,
        reserved_qty=0,
    )
    db.add(variant)
    db.flush()

    order = Order(
        customer_id=customer.id,
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
        address="Москва, улица Тестовая, 1",
        comment="Позвонить за 5 минут",
        created_at=moment,
    )
    db.add(order)
    db.flush()
    item = OrderItem(
        order_id=order.id,
        product_id=product.id,
        variant_id=variant.id,
        title="Куртка",
        size="M",
        quantity=1,
        price=Decimal("1234.56"),
    )
    payment = Payment(
        order_id=order.id,
        provider="yookassa",
        provider_payment_id="privacy-payment",
        status="succeeded",
        amount=Decimal("1234.56"),
        created_at=moment,
    )
    ret = ReturnRequest(
        order_id=order.id,
        customer_id=customer.id,
        reason="Размер не подошёл",
        status="requested",
        refund_amount=Decimal("12.34"),
        created_at=moment,
    )
    db.add_all([item, payment, ret])

    cart = Cart(
        customer_id=customer.id,
        status="converted",
        referral_code="PRIVACYREF",
        loyalty_points_to_redeem=Decimal("12.3400"),
        created_at=moment,
        updated_at=moment,
    )
    db.add(cart)
    db.flush()
    db.add(
        CartItem(
            cart_id=cart.id,
            product_id=product.id,
            variant_id=variant.id,
            quantity=1,
            created_at=moment,
        )
    )

    db.add_all(
        [
            ConsentRecord(
                customer_id=customer.id,
                consent_type="privacy",
                granted=True,
                source="telegram_mini_app",
                created_at=moment,
            ),
            PrivacyRequest(
                customer_id=customer.id,
                request_type="export",
                status="requested",
                created_at=moment,
                processed_at=None,
            ),
            LoyaltyTransaction(
                customer_id=customer.id,
                order_id=order.id,
                points_delta=Decimal("1.2500"),
                reason="order_paid",
                created_at=moment,
            ),
            CrmProfile(
                customer_id=customer.id,
                segment="active",
                orders_count=1,
                total_spent=Decimal("1234.56"),
                average_order_value=Decimal("1234.56"),
                loyalty_points=Decimal("42.1250"),
                vip=False,
                last_order_at=moment,
                updated_at=moment,
            ),
            ReferralCode(
                customer_id=customer.id,
                code="PRIVACYREF",
                reward_points=Decimal("500.0000"),
                used_count=0,
                active=True,
                created_at=moment,
            ),
            SupportTicket(
                customer_id=customer.id,
                order_id=order.id,
                subject="Возврат 🚀",
                message="Проверка Unicode: привет",
                status="open",
                priority="normal",
                created_at=moment,
                updated_at=moment,
            ),
            CustomerTimelineEvent(
                customer_id=customer.id,
                event_type="privacy.test",
                title="Экспорт подготовлен",
                payload='{"пример":"да"}',
                created_at=moment,
            ),
            Notification(
                telegram_id=customer.telegram_id,
                message="Ваш заказ готов",
                status="sent",
                created_at=moment,
                sent_at=moment,
            ),
            SupportTicket(
                customer_id=other.id,
                subject="DO-NOT-EXPORT",
                message="OTHER-CUSTOMER-SECRET",
                status="open",
                priority="normal",
                created_at=moment,
                updated_at=moment,
            ),
        ]
    )

    session = CustomerSession(
        customer_id=customer.id,
        session_id_hash="a" * 64,
        jti_hash="b" * 64,
        created_at=moment,
        expires_at=moment + timedelta(days=1),
        revoked_at=None,
        revoke_reason="",
    )
    db.add(session)
    db.flush()
    db.add(
        CustomerAuthEvent(
            customer_id=customer.id,
            session_id=session.id,
            event_type="session.created",
            created_at=moment,
        )
    )
    db.commit()
    return customer, other


def test_privacy_export_contract_is_exact_unicode_safe_and_customer_scoped():
    _engine, db = _db()
    customer, other = _seed_customer_export(db)

    exported = build_customer_export(
        db,
        customer,
        generated_at=datetime(2026, 9, 19, 13, 0, 0),
    )

    assert exported["schema_version"] == PRIVACY_EXPORT_SCHEMA_VERSION
    assert exported["generated_at"] == "2026-09-19T13:00:00.000000Z"
    assert exported["representation"] == {
        "money": "decimal-string-2dp",
        "loyalty_points": "decimal-string-4dp",
        "timestamps": "ISO-8601 UTC string",
        "nullable": "JSON null",
        "ordering": "ascending local record id",
    }
    assert exported["customer"]["first_name"] == "Пётр 🚀"
    assert exported["customer"]["created_at"] == "2026-09-19T12:34:56.123456Z"

    order = exported["orders"][0]
    assert order["total_amount"] == "1234.56"
    assert order["loyalty_points_redeemed"] == "0.1250"
    assert order["items"][0]["price"] == "1234.56"
    assert order["payments"][0]["amount"] == "1234.56"
    assert order["returns"][0]["refund_amount"] == "12.34"

    assert exported["carts"][0]["loyalty_points_to_redeem"] == "12.3400"
    assert exported["loyalty"]["profile"]["total_spent"] == "1234.56"
    assert exported["loyalty"]["profile"]["loyalty_points"] == "42.1250"
    assert exported["loyalty"]["transactions"][0]["points_delta"] == "1.2500"
    assert exported["referral_codes"][0]["reward_points"] == "500.0000"
    assert exported["privacy_requests"][0]["processed_at"] is None
    assert exported["support_tickets"][0]["message"] == "Проверка Unicode: привет"
    assert exported["notifications"][0]["message"] == "Ваш заказ готов"

    assert exported["authentication"]["sessions"][0]["id"] > 0
    assert "session_id_hash" not in exported["authentication"]["sessions"][0]
    assert "jti_hash" not in exported["authentication"]["sessions"][0]
    assert exported["authentication"]["events"][0]["event_type"] == "session.created"

    raw = json.dumps(exported, ensure_ascii=False)
    assert "OTHER-CUSTOMER-SECRET" not in raw
    assert str(other.id) not in {
        str(ticket["id"]) for ticket in exported["support_tickets"]
    }


def test_privacy_export_batch_iterator_is_keyset_bounded_and_stable():
    _engine, db = _db()
    customer = Customer(telegram_id="privacy-batch")
    db.add(customer)
    db.flush()
    for index in range(5):
        db.add(
            ConsentRecord(
                customer_id=customer.id,
                consent_type="analytics",
                granted=bool(index % 2),
                source=f"batch-{index}",
            )
        )
    db.commit()

    rows = list(
        _iter_customer_rows(
            db,
            ConsentRecord,
            customer_id=customer.id,
            batch_size=2,
        )
    )

    assert len(rows) == 5
    assert [row.id for row in rows] == sorted(row.id for row in rows)
    assert [row.source for row in rows] == [
        "batch-0",
        "batch-1",
        "batch-2",
        "batch-3",
        "batch-4",
    ]


def test_anonymized_customer_export_is_explicitly_unavailable():
    _engine, db = _db()
    customer = Customer(telegram_id="deleted:99:abcdef")
    db.add(customer)
    db.commit()

    with pytest.raises(
        PrivacyExportUnavailable,
        match="Anonymized customer identities cannot be exported",
    ):
        build_customer_export(db, customer)
