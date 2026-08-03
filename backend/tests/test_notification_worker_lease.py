from datetime import datetime
from types import SimpleNamespace

from backend.services import notification_delivery


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


def test_stale_worker_cannot_finish_reclaimed_notification():
    stale_token = "stale-token"
    replacement_token = "replacement-token"
    row = SimpleNamespace(id=11, status="processing", sent_at=None, error="")
    state = SimpleNamespace(
        lease_token=replacement_token,
        next_attempt_at=datetime(2026, 8, 1, 10, 3, 0),
        attempts=1,
    )
    session = FakeSession(row, state)

    outcome = notification_delivery.finish_delivery(
        session,
        row.id,
        stale_token,
    )

    assert outcome == "ignored"
    assert row.status == "processing"
    assert row.sent_at is None
    assert state.lease_token == replacement_token
    assert session.rollbacks == 1
    assert session.commits == 0
    assert session.deleted == []


def test_current_token_owner_can_mark_notification_sent(monkeypatch):
    lease_token = "current-token"
    completed_at = datetime(2026, 8, 1, 10, 1, 30)
    row = SimpleNamespace(id=12, status="processing", sent_at=None, error="previous")
    state = SimpleNamespace(
        lease_token=lease_token,
        next_attempt_at=datetime(2026, 8, 1, 10, 3, 0),
        attempts=0,
    )
    session = FakeSession(row, state)
    monkeypatch.setattr(notification_delivery, "utcnow_naive", lambda: completed_at)

    outcome = notification_delivery.finish_delivery(
        session,
        row.id,
        lease_token,
    )

    assert outcome == "sent"
    assert row.status == "sent"
    assert row.sent_at == completed_at
    assert row.error == ""
    assert session.commits == 1
    assert session.rollbacks == 0
    assert session.deleted == [state]
