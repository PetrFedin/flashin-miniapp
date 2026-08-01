from datetime import datetime, timedelta
from types import SimpleNamespace

from bot import send_notifications


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
        self.closed = 0

    def query(self, model):
        return FakeQuery(self.values.pop(0))

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def delete(self, value):
        self.deleted.append(value)

    def close(self):
        self.closed += 1


def test_stale_worker_cannot_finish_reclaimed_notification(monkeypatch):
    original_lease = datetime(2026, 8, 1, 10, 0, 0)
    replacement_lease = original_lease + timedelta(minutes=3)
    row = SimpleNamespace(id=11, status="processing", sent_at=None, error="")
    state = SimpleNamespace(next_attempt_at=replacement_lease, attempts=1)
    session = FakeSession(row, state)
    monkeypatch.setattr(send_notifications, "SessionLocal", lambda: session)

    outcome = send_notifications._finish_delivery(11, original_lease)

    assert outcome == "ignored"
    assert row.status == "processing"
    assert row.sent_at is None
    assert state.next_attempt_at == replacement_lease
    assert session.rollbacks == 1
    assert session.commits == 0
    assert session.deleted == []
    assert session.closed == 1


def test_current_lease_owner_can_mark_notification_sent(monkeypatch):
    lease_until = datetime(2026, 8, 1, 10, 3, 0)
    completed_at = datetime(2026, 8, 1, 10, 1, 30)
    row = SimpleNamespace(id=12, status="processing", sent_at=None, error="previous")
    state = SimpleNamespace(next_attempt_at=lease_until, attempts=0)
    session = FakeSession(row, state)
    monkeypatch.setattr(send_notifications, "SessionLocal", lambda: session)
    monkeypatch.setattr(send_notifications, "utcnow_naive", lambda: completed_at)

    outcome = send_notifications._finish_delivery(12, lease_until)

    assert outcome == "sent"
    assert row.status == "sent"
    assert row.sent_at == completed_at
    assert row.error == ""
    assert session.commits == 1
    assert session.rollbacks == 0
    assert session.deleted == [state]
    assert session.closed == 1
