from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from backend.api.admin_notifications import FILTERABLE_NOTIFICATION_STATUSES
from backend.services import notification_delivery
from bot import send_notifications as worker


class FakeQuery:
    def __init__(self, value):
        self.value = value

    def filter(self, *args, **kwargs):
        return self

    def with_for_update(self):
        return self

    def first(self):
        return self.value


class FakeSession:
    def __init__(self, row, state):
        self.values = [row, state]
        self.commits = 0
        self.rollbacks = 0
        self.deleted = []

    def query(self, model):
        return FakeQuery(self.values.pop(0))

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def delete(self, value):
        self.deleted.append(value)


def _leased_notification():
    row = SimpleNamespace(
        id=101,
        status="processing",
        sent_at=None,
        error="",
    )
    state = SimpleNamespace(
        lease_token="lease-token",
        next_attempt_at=datetime(2026, 9, 6, 17, 30, 0),
        attempts=0,
        updated_at=None,
        last_error="",
    )
    return row, state


def test_unknown_transport_error_defaults_to_review_required(monkeypatch):
    row, state = _leased_notification()
    session = FakeSession(row, state)
    monkeypatch.setattr(
        notification_delivery,
        "utcnow_naive",
        lambda: datetime(2026, 9, 6, 17, 25, 0),
    )

    outcome = notification_delivery.finish_delivery(
        session,
        row.id,
        state.lease_token,
        error=RuntimeError("response lost after provider acceptance"),
    )

    assert outcome == "review_required"
    assert row.status == "review_required"
    assert row.sent_at is None
    assert "RuntimeError" in row.error
    assert state.attempts == 1
    assert state.next_attempt_at is None
    assert state.lease_token is None
    assert session.commits == 1
    assert session.rollbacks == 0
    assert session.deleted == []


def test_explicit_retryable_failure_schedules_retry(monkeypatch):
    row, state = _leased_notification()
    session = FakeSession(row, state)
    monkeypatch.setattr(
        notification_delivery,
        "utcnow_naive",
        lambda: datetime(2026, 9, 6, 17, 25, 0),
    )

    outcome = notification_delivery.finish_delivery(
        session,
        row.id,
        state.lease_token,
        error=RuntimeError("provider asked to retry"),
        delivery_outcome=notification_delivery.DELIVERY_OUTCOME_RETRYABLE_FAILURE,
    )

    assert outcome == "retry_scheduled"
    assert row.status == "pending"
    assert state.attempts == 1
    assert state.next_attempt_at is not None
    assert state.lease_token is None
    assert session.commits == 1


def test_explicit_permanent_failure_is_terminal(monkeypatch):
    row, state = _leased_notification()
    session = FakeSession(row, state)
    monkeypatch.setattr(
        notification_delivery,
        "utcnow_naive",
        lambda: datetime(2026, 9, 6, 17, 25, 0),
    )

    outcome = notification_delivery.finish_delivery(
        session,
        row.id,
        state.lease_token,
        error=ValueError("invalid Telegram destination"),
        delivery_outcome=notification_delivery.DELIVERY_OUTCOME_PERMANENT_FAILURE,
    )

    assert outcome == "failed"
    assert row.status == "failed"
    assert state.attempts == 1
    assert state.next_attempt_at is None
    assert state.lease_token is None
    assert session.commits == 1


def test_review_required_can_only_return_to_pending_by_explicit_reset():
    notification = SimpleNamespace(
        id=33,
        status="review_required",
        error="RuntimeError: ambiguous delivery",
        sent_at=None,
    )
    state = SimpleNamespace(
        id=9,
        notification_id=33,
        attempts=1,
        next_attempt_at=None,
        lease_token=None,
        last_error="RuntimeError: ambiguous delivery",
        updated_at=None,
    )
    reset_at = datetime(2026, 9, 6, 18, 0, 0)

    result = notification_delivery.reset_notification_delivery(
        notification,
        state,
        now=reset_at,
    )

    assert result is state
    assert notification.status == "pending"
    assert notification.error == ""
    assert state.attempts == 0
    assert state.next_attempt_at == reset_at
    assert state.lease_token is None
    assert state.last_error == ""


def test_worker_classifies_only_explicit_retry_as_automatic(monkeypatch):
    class ApiError(Exception):
        pass

    class NetworkError(ApiError):
        pass

    class ServerError(ApiError):
        pass

    class RetryAfter(ApiError):
        pass

    monkeypatch.setattr(worker, "TelegramAPIError", ApiError)
    monkeypatch.setattr(worker, "TelegramNetworkError", NetworkError)
    monkeypatch.setattr(worker, "TelegramServerError", ServerError)
    monkeypatch.setattr(worker, "TelegramRetryAfter", RetryAfter)

    assert (
        worker.classify_telegram_delivery_error(RetryAfter())
        == notification_delivery.DELIVERY_OUTCOME_RETRYABLE_FAILURE
    )
    assert (
        worker.classify_telegram_delivery_error(NetworkError())
        == notification_delivery.DELIVERY_OUTCOME_REVIEW_REQUIRED
    )
    assert (
        worker.classify_telegram_delivery_error(ServerError())
        == notification_delivery.DELIVERY_OUTCOME_REVIEW_REQUIRED
    )
    assert (
        worker.classify_telegram_delivery_error(ApiError())
        == notification_delivery.DELIVERY_OUTCOME_PERMANENT_FAILURE
    )
    assert (
        worker.classify_telegram_delivery_error(RuntimeError("unknown transport failure"))
        == notification_delivery.DELIVERY_OUTCOME_REVIEW_REQUIRED
    )


def test_review_required_is_operator_visible_but_not_bulk_failed_requeue():
    assert "review_required" in FILTERABLE_NOTIFICATION_STATUSES

    source = (
        Path(__file__).resolve().parents[1] / "api" / "admin_notifications.py"
    ).read_text(encoding="utf-8")
    bulk_start = source.index("def requeue_failed_notifications(")
    single_start = source.index("def requeue_notification(")
    bulk_source = source[bulk_start:single_start]

    assert '.filter(Notification.status == "failed")' in bulk_source
    assert 'Notification.status == "review_required"' not in bulk_source
