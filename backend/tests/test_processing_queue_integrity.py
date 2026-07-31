from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.database import Base
from backend.jobs import campaign_jobs, media_jobs
from backend.models import (
    BusinessEvent,
    CrmProfile,
    Customer,
    MarketingCampaign,
    MediaAsset,
    MediaProcessingJob,
    Notification,
    WebhookDestination,
    WebhookOutbox,
)
from backend.notification_models import NotificationDeliveryState
from backend.services import event_dispatcher
from backend.services.campaigns import queue_campaign


def _factory():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autocommit=False, autoflush=False)


def _event(**overrides) -> BusinessEvent:
    values = {
        "event_type": "order.created",
        "aggregate_type": "order",
        "aggregate_id": "42",
        "payload_json": '{"order_id":42}',
        "status": "pending",
        "attempts": 0,
        "next_attempt_at": datetime.utcnow(),
        "lease_token": "",
        "lease_expires_at": None,
        "last_error": "",
        "processed_at": None,
    }
    values.update(overrides)
    return BusinessEvent(**values)


def _asset(storage_key: str = "asset.png") -> MediaAsset:
    return MediaAsset(
        url=f"https://cdn.example.com/{storage_key}",
        storage_key=storage_key,
        filename=storage_key,
        content_type="image/png",
        size_bytes=128,
    )


def _media_job(asset_id: int, **overrides) -> MediaProcessingJob:
    values = {
        "media_asset_id": asset_id,
        "status": "pending",
        "attempts": 0,
        "next_attempt_at": datetime.utcnow(),
        "lease_token": "",
        "lease_expires_at": None,
        "last_error": "",
        "processed_at": None,
    }
    values.update(overrides)
    return MediaProcessingJob(**values)


def _destination() -> WebhookDestination:
    return WebhookDestination(
        name="Order events",
        url="https://example.com/webhooks/orders",
        event_type="order.created",
        active=True,
        signing_secret="x" * 32,
    )


def test_queue_metadata_contains_due_and_active_job_guards():
    event_checks = {
        constraint.name for constraint in BusinessEvent.__table__.constraints
    }
    media_checks = {
        constraint.name for constraint in MediaProcessingJob.__table__.constraints
    }
    media_indexes = {index.name for index in MediaProcessingJob.__table__.indexes}

    assert "ck_business_events_state_coherent" in event_checks
    assert "ck_media_processing_jobs_state_coherent" in media_checks
    assert "uq_media_processing_jobs_active_asset" in media_indexes


def test_direct_sql_rejects_processing_event_without_lease():
    db = _factory()()

    with pytest.raises(IntegrityError):
        db.execute(
            BusinessEvent.__table__.insert().values(
                event_type="order.created",
                aggregate_type="order",
                aggregate_id="42",
                payload_json="{}",
                status="processing",
                attempts=0,
                created_at=datetime.utcnow(),
                processed_at=None,
                next_attempt_at=None,
                lease_token="",
                lease_expires_at=None,
                last_error="",
            )
        )
        db.commit()
    db.rollback()


def test_event_dispatch_success_is_leased_atomic_and_idempotent():
    db = _factory()()
    db.add(_destination())
    event_dispatcher.emit_event(
        db,
        "order.created",
        aggregate_type="order",
        aggregate_id=42,
        payload={"order_id": 42},
    )
    db.commit()

    assert event_dispatcher.process_pending_events(db) == 1
    assert event_dispatcher.process_pending_events(db) == 0

    event = db.query(BusinessEvent).one()
    outbox = db.query(WebhookOutbox).one()
    assert event.status == "processed"
    assert event.attempts == 0
    assert event.processed_at is not None
    assert event.next_attempt_at is None
    assert event.lease_token == ""
    assert event.last_error == ""
    assert outbox.event_type == "order.created"
    assert outbox.status == "pending"


def test_event_transient_failure_has_backoff_and_no_immediate_replay(monkeypatch):
    db = _factory()()
    event_dispatcher.emit_event(db, "order.created", payload={"order_id": 42})
    db.commit()
    started_at = datetime.utcnow()

    def fail(*_args, **_kwargs):
        raise RuntimeError("destination temporarily unavailable")

    monkeypatch.setattr(event_dispatcher, "enqueue_event_for_destinations", fail)

    assert event_dispatcher.process_pending_events(db) == 0
    event = db.query(BusinessEvent).one()
    assert event.status == "pending"
    assert event.attempts == 1
    assert "temporarily unavailable" in event.last_error
    assert event.next_attempt_at > started_at
    assert event.lease_token == ""
    assert event_dispatcher.process_pending_events(db) == 0
    assert db.query(BusinessEvent).one().attempts == 1


def test_invalid_event_payload_fails_terminally_without_outbox():
    db = _factory()()
    db.add(_destination())
    db.add(_event(payload_json="{not-json"))
    db.commit()

    assert event_dispatcher.process_pending_events(db) == 0

    event = db.query(BusinessEvent).one()
    assert event.status == "failed"
    assert event.attempts == 10
    assert event.next_attempt_at is None
    assert event.last_error
    assert db.query(WebhookOutbox).count() == 0


def test_event_failure_rolls_back_partially_enqueued_outbox(monkeypatch):
    db = _factory()()
    event_dispatcher.emit_event(db, "order.created", payload={"order_id": 42})
    db.commit()

    def add_then_fail(session, event_type, payload):
        session.add(
            WebhookOutbox(
                destination="https://example.com/webhooks/orders",
                event_type=event_type,
                payload='{"order_id":42}',
                status="pending",
                attempts=0,
                next_attempt_at=datetime.utcnow(),
                last_error="",
            )
        )
        session.flush()
        raise RuntimeError("outbox fanout failed")

    monkeypatch.setattr(
        event_dispatcher,
        "enqueue_event_for_destinations",
        add_then_fail,
    )

    assert event_dispatcher.process_pending_events(db) == 0
    assert db.query(WebhookOutbox).count() == 0
    event = db.query(BusinessEvent).one()
    assert event.status == "pending"
    assert event.attempts == 1


def test_expired_event_lease_is_recovered_as_failed_attempt():
    db = _factory()()
    db.add(
        _event(
            status="processing",
            attempts=2,
            next_attempt_at=None,
            lease_token="a" * 32,
            lease_expires_at=datetime.utcnow() - timedelta(seconds=1),
            last_error="previous failure",
        )
    )
    db.commit()

    assert event_dispatcher.process_pending_events(db) == 0
    event = db.query(BusinessEvent).one()
    assert event.status == "pending"
    assert event.attempts == 3
    assert "lease expired" in event.last_error.lower()
    assert event.next_attempt_at > datetime.utcnow()
    assert event.lease_token == ""


def test_wrong_event_fencing_token_cannot_complete_claim():
    db = _factory()()
    db.add(
        _event(
            status="processing",
            next_attempt_at=None,
            lease_token="a" * 32,
            lease_expires_at=datetime.utcnow() + timedelta(minutes=5),
        )
    )
    db.commit()
    event_id = db.query(BusinessEvent.id).scalar()

    assert event_dispatcher._process_claim(db, event_id, "b" * 32) is False
    event = db.query(BusinessEvent).one()
    assert event.status == "processing"
    assert event.lease_token == "a" * 32


def test_media_queue_creates_one_job_per_asset():
    db = _factory()()
    db.add(_asset())
    db.commit()

    assert media_jobs.queue_missing_media_jobs(db) == 1
    assert media_jobs.queue_missing_media_jobs(db) == 0

    job = db.query(MediaProcessingJob).one()
    assert job.status == "pending"
    assert job.attempts == 0
    assert job.next_attempt_at is not None


def test_media_job_success_is_fenced_and_terminal(monkeypatch):
    db = _factory()()
    asset = _asset()
    db.add(asset)
    db.commit()
    db.add(_media_job(asset.id))
    db.commit()
    monkeypatch.setattr(media_jobs, "generate_local_derivatives", lambda *_args: [])

    assert media_jobs.process_media_jobs(db) == 1
    assert media_jobs.process_media_jobs(db) == 0

    job = db.query(MediaProcessingJob).one()
    assert job.status == "processed"
    assert job.processed_at is not None
    assert job.next_attempt_at is None
    assert job.lease_token == ""
    assert job.last_error == ""


def test_media_failure_uses_backoff_and_preserves_error(monkeypatch):
    db = _factory()()
    asset = _asset()
    db.add(asset)
    db.commit()
    db.add(_media_job(asset.id))
    db.commit()
    started_at = datetime.utcnow()

    def fail(*_args):
        raise RuntimeError("image decoder unavailable")

    monkeypatch.setattr(media_jobs, "generate_local_derivatives", fail)

    assert media_jobs.process_media_jobs(db) == 0
    job = db.query(MediaProcessingJob).one()
    assert job.status == "pending"
    assert job.attempts == 1
    assert "decoder unavailable" in job.last_error
    assert job.next_attempt_at > started_at
    assert media_jobs.process_media_jobs(db) == 0
    assert db.query(MediaProcessingJob).one().attempts == 1


def test_missing_media_asset_is_terminal_failure():
    db = _factory()()
    db.add(_media_job(999))
    db.commit()

    assert media_jobs.process_media_jobs(db) == 0
    job = db.query(MediaProcessingJob).one()
    assert job.status == "failed"
    assert job.attempts == 5
    assert job.next_attempt_at is None
    assert "not found" in job.last_error.lower()


def test_expired_media_lease_is_recovered_with_backoff():
    db = _factory()()
    asset = _asset()
    db.add(asset)
    db.commit()
    db.add(
        _media_job(
            asset.id,
            status="processing",
            attempts=1,
            next_attempt_at=None,
            lease_token="c" * 32,
            lease_expires_at=datetime.utcnow() - timedelta(seconds=1),
            last_error="previous failure",
        )
    )
    db.commit()

    assert media_jobs.process_media_jobs(db) == 0
    job = db.query(MediaProcessingJob).one()
    assert job.status == "pending"
    assert job.attempts == 2
    assert "lease expired" in job.last_error.lower()
    assert job.next_attempt_at > datetime.utcnow()


def test_database_blocks_two_active_media_jobs_for_one_asset():
    db = _factory()()
    asset = _asset()
    db.add(asset)
    db.commit()
    db.add(_media_job(asset.id))
    db.commit()

    with pytest.raises(IntegrityError):
        db.execute(
            MediaProcessingJob.__table__.insert().values(
                media_asset_id=asset.id,
                status="pending",
                attempts=0,
                last_error="",
                created_at=datetime.utcnow(),
                processed_at=None,
                next_attempt_at=datetime.utcnow(),
                lease_token="",
                lease_expires_at=None,
            )
        )
        db.commit()
    db.rollback()
    assert db.query(MediaProcessingJob).count() == 1


def test_campaign_uses_notification_delivery_deduplication():
    db = _factory()()
    db.add_all(
        [
            Customer(telegram_id="10001", first_name="A"),
            Customer(telegram_id="10002", first_name="B"),
        ]
    )
    campaign = MarketingCampaign(
        name="Drop",
        segment="all",
        message="New drop is live",
        status="draft",
        sent_count=0,
    )
    db.add(campaign)
    db.commit()

    assert queue_campaign(db, campaign) == 2
    db.commit()
    assert queue_campaign(db, campaign) == 2
    db.commit()

    assert db.query(Notification).count() == 2
    states = db.query(NotificationDeliveryState).order_by(NotificationDeliveryState.id).all()
    assert len(states) == 2
    assert {state.deduplication_key for state in states} == {
        f"campaign:{campaign.id}:customer:1",
        f"campaign:{campaign.id}:customer:2",
    }
    assert campaign.status == "queued"
    assert campaign.sent_count == 2
    assert campaign.sent_at is not None


def test_campaign_segment_filter_queues_only_matching_customers():
    db = _factory()()
    first = Customer(telegram_id="20001")
    second = Customer(telegram_id="20002")
    db.add_all([first, second])
    db.flush()
    db.add_all(
        [
            CrmProfile(customer_id=first.id, segment="vip"),
            CrmProfile(customer_id=second.id, segment="new"),
        ]
    )
    campaign = MarketingCampaign(
        name="VIP",
        segment="vip",
        message="Private access",
        status="draft",
        sent_count=0,
    )
    db.add(campaign)
    db.commit()

    assert queue_campaign(db, campaign) == 1
    db.commit()
    assert db.query(Notification).one().telegram_id == "20001"


def test_bad_scheduled_campaign_does_not_block_valid_campaign():
    db = _factory()()
    db.add(Customer(telegram_id="30001"))
    now = datetime.utcnow() - timedelta(minutes=1)
    bad = MarketingCampaign(
        name="Broken",
        segment="all",
        message="   ",
        status="scheduled",
        sent_count=0,
        scheduled_at=now,
    )
    good = MarketingCampaign(
        name="Ready",
        segment="all",
        message="Valid campaign",
        status="scheduled",
        sent_count=0,
        scheduled_at=now,
    )
    db.add_all([bad, good])
    db.commit()

    assert campaign_jobs.queue_due_campaigns(db) == 1
    db.refresh(bad)
    db.refresh(good)
    assert bad.status == "failed"
    assert good.status == "queued"
    assert db.query(Notification).count() == 1
