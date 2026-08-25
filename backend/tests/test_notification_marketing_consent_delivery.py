from datetime import timedelta
from pathlib import Path

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.database import Base, utcnow_naive
from backend.models import ConsentRecord, Customer, MarketingCampaign, Notification
from backend.notification_models import (
    NotificationDeliveryState,
    NotificationEventKey,
    NotificationPolicyContext,
)
from backend.services.notification_delivery import (
    DELIVERY_ALLOWED,
    DELIVERY_SUPPRESSED,
    preflight_notification_delivery,
    reset_notification_delivery,
)
from backend.services.notifications import (
    NOTIFICATION_PURPOSE_MARKETING,
    NOTIFICATION_PURPOSE_TRANSACTIONAL,
    queue_notification,
)

ROOT = Path(__file__).resolve().parents[2]
BOT_WORKER = ROOT / "bot" / "send_notifications.py"
CAMPAIGNS = ROOT / "backend" / "services" / "campaigns.py"
OPS_JOBS = ROOT / "backend" / "jobs" / "ops_jobs.py"
ADMIN_NOTIFICATIONS = ROOT / "backend" / "api" / "admin_notifications.py"
MIGRATION = ROOT / "backend" / "alembic" / "versions" / "0034_notification_policy_context.py"


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(
        engine,
        tables=[
            Customer.__table__,
            MarketingCampaign.__table__,
            Notification.__table__,
            ConsentRecord.__table__,
            NotificationDeliveryState.__table__,
            NotificationEventKey.__table__,
            NotificationPolicyContext.__table__,
        ],
    )
    session = sessionmaker(bind=engine, autocommit=False, autoflush=False)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _customer(db, telegram_id: str = "123456") -> Customer:
    customer = Customer(telegram_id=telegram_id)
    db.add(customer)
    db.flush()
    return customer


def _processing_marketing_notification(db, customer: Customer) -> tuple[Notification, str]:
    notification = Notification(
        telegram_id=customer.telegram_id,
        message="Marketing message",
        status="processing",
    )
    db.add(notification)
    db.flush()
    db.add(
        NotificationPolicyContext(
            notification_id=notification.id,
            purpose=NOTIFICATION_PURPOSE_MARKETING,
            customer_id=customer.id,
        )
    )
    lease_token = "lease-token"
    db.add(
        NotificationDeliveryState(
            notification_id=notification.id,
            attempts=0,
            next_attempt_at=utcnow_naive() + timedelta(minutes=1),
            lease_token=lease_token,
        )
    )
    db.commit()
    return notification, lease_token


def _consent(db, customer: Customer, granted: bool, *, created_at=None) -> ConsentRecord:
    row = ConsentRecord(
        customer_id=customer.id,
        consent_type="marketing",
        granted=granted,
        source="test",
        created_at=created_at or utcnow_naive(),
    )
    db.add(row)
    db.commit()
    return row


def test_marketing_notification_is_suppressed_when_consent_was_revoked_after_enqueue(db):
    customer = _customer(db)
    _consent(db, customer, True, created_at=utcnow_naive() - timedelta(minutes=2))
    notification, lease_token = _processing_marketing_notification(db, customer)
    _consent(db, customer, False, created_at=utcnow_naive() - timedelta(minutes=1))

    outcome = preflight_notification_delivery(db, notification.id, lease_token)

    assert outcome == DELIVERY_SUPPRESSED
    db.refresh(notification)
    assert notification.status == "suppressed"
    assert notification.sent_at is None
    assert "consent" in notification.error.lower()
    assert (
        db.query(NotificationDeliveryState)
        .filter(NotificationDeliveryState.notification_id == notification.id)
        .first()
        is None
    )


def test_latest_regrant_allows_marketing_delivery(db):
    customer = _customer(db)
    now = utcnow_naive()
    _consent(db, customer, False, created_at=now - timedelta(minutes=2))
    _consent(db, customer, True, created_at=now - timedelta(minutes=1))
    notification, lease_token = _processing_marketing_notification(db, customer)

    outcome = preflight_notification_delivery(db, notification.id, lease_token)

    assert outcome == DELIVERY_ALLOWED
    db.refresh(notification)
    assert notification.status == "processing"


def test_marketing_without_customer_binding_fails_closed(db):
    notification = Notification(telegram_id="123456", message="Marketing", status="processing")
    db.add(notification)
    db.flush()
    db.add(
        NotificationPolicyContext(
            notification_id=notification.id,
            purpose=NOTIFICATION_PURPOSE_MARKETING,
            customer_id=None,
        )
    )
    db.add(
        NotificationDeliveryState(
            notification_id=notification.id,
            attempts=0,
            lease_token="lease-token",
        )
    )
    db.commit()

    outcome = preflight_notification_delivery(db, notification.id, "lease-token")

    assert outcome == DELIVERY_SUPPRESSED
    db.refresh(notification)
    assert notification.status == "suppressed"


def test_transactional_notification_ignores_marketing_consent(db):
    customer = _customer(db)
    _consent(db, customer, False)
    notification = Notification(
        telegram_id=customer.telegram_id,
        message="Order update",
        status="processing",
    )
    db.add(notification)
    db.flush()
    db.add(
        NotificationPolicyContext(
            notification_id=notification.id,
            purpose=NOTIFICATION_PURPOSE_TRANSACTIONAL,
            customer_id=customer.id,
        )
    )
    db.add(
        NotificationDeliveryState(
            notification_id=notification.id,
            attempts=0,
            lease_token="lease-token",
        )
    )
    db.commit()

    outcome = preflight_notification_delivery(db, notification.id, "lease-token")

    assert outcome == DELIVERY_ALLOWED
    db.refresh(notification)
    assert notification.status == "processing"


def test_legacy_notification_without_policy_context_remains_transactional_compatible(db):
    notification = Notification(telegram_id="123456", message="Legacy order update", status="processing")
    db.add(notification)
    db.flush()
    db.add(
        NotificationDeliveryState(
            notification_id=notification.id,
            attempts=0,
            lease_token="lease-token",
        )
    )
    db.commit()

    assert preflight_notification_delivery(db, notification.id, "lease-token") == DELIVERY_ALLOWED


def test_suppressed_notification_cannot_be_manually_retried():
    notification = Notification(
        id=901,
        telegram_id="123456",
        message="Marketing",
        status="suppressed",
        error="Suppressed by marketing consent policy",
    )

    with pytest.raises(HTTPException) as exc_info:
        reset_notification_delivery(notification)

    assert exc_info.value.status_code == 409
    assert "cannot be retried" in str(exc_info.value.detail)


def test_queue_helper_persists_explicit_marketing_context(db):
    customer = _customer(db)
    campaign = MarketingCampaign(name="Drop", segment="all", message="New drop")
    db.add(campaign)
    db.flush()

    queued = queue_notification(
        db,
        customer.telegram_id,
        campaign.message,
        purpose=NOTIFICATION_PURPOSE_MARKETING,
        customer_id=customer.id,
        campaign_id=campaign.id,
    )
    db.commit()

    assert queued is True
    context = db.query(NotificationPolicyContext).one()
    assert context.purpose == NOTIFICATION_PURPOSE_MARKETING
    assert context.customer_id == customer.id
    assert context.campaign_id == campaign.id


def test_queue_helper_rejects_unbound_marketing_notification(db):
    with pytest.raises(ValueError, match="requires customer_id"):
        queue_notification(
            db,
            "123456",
            "Marketing",
            purpose=NOTIFICATION_PURPOSE_MARKETING,
        )


def test_transport_worker_checks_policy_immediately_before_telegram_side_effect():
    source = BOT_WORKER.read_text(encoding="utf-8")
    preflight_index = source.index(
        "preflight = _preflight_notification_delivery(notification_id, lease_token)"
    )
    send_index = source.index("await bot.send_message(")
    assert preflight_index < send_index
    assert 'result["suppressed"] += 1' in source
    assert "if preflight != DELIVERY_ALLOWED" in source


def test_all_known_marketing_producers_use_canonical_purpose_binding():
    campaign_source = CAMPAIGNS.read_text(encoding="utf-8")
    assert "queue_notification(" in campaign_source
    assert "purpose=NOTIFICATION_PURPOSE_MARKETING" in campaign_source
    assert "customer_id=customer.id" in campaign_source
    assert "campaign_id=locked.id" in campaign_source
    assert "Notification(" not in campaign_source

    ops_source = OPS_JOBS.read_text(encoding="utf-8")
    assert "queue_notification(" in ops_source
    assert "purpose=NOTIFICATION_PURPOSE_MARKETING" in ops_source
    assert "customer_id=cart.customer_id" in ops_source
    assert "Notification(" not in ops_source


def test_policy_context_is_not_exposed_by_admin_notification_serializer():
    source = ADMIN_NOTIFICATIONS.read_text(encoding="utf-8")
    assert "NotificationPolicyContext" not in source
    assert '"customer_id"' not in source
    assert '"campaign_id"' not in source


def test_notification_policy_migration_extends_current_revision_chain():
    source = MIGRATION.read_text(encoding="utf-8")
    assert 'revision = "0034_notification_policy_context"' in source
    assert 'down_revision = "0033_admin_totp_replay_state"' in source
    assert '"notification_policy_contexts"' in source
    assert "ck_notification_policy_context_purpose" in source
