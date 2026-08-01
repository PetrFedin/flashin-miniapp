import pytest
from fastapi import HTTPException

from backend.api import health


CURRENT_HEAD = "0017_promo_definition_constraints"


class ScalarResult:
    def __init__(self, values=None):
        self.values = list(values or [])

    def scalars(self):
        return self

    def all(self):
        return self.values


class FakeDb:
    def __init__(self, revisions):
        self.revisions = revisions
        self.rollbacks = 0

    def execute(self, statement):
        sql = str(statement)
        if "version_num" in sql:
            return ScalarResult(self.revisions)
        return ScalarResult()

    def rollback(self):
        self.rollbacks += 1


def test_repository_has_single_current_migration_head():
    assert health._expected_migration_heads() == frozenset({CURRENT_HEAD})


def test_ready_when_database_revision_matches(monkeypatch):
    monkeypatch.setattr(
        health,
        "_expected_migration_heads",
        lambda: frozenset({CURRENT_HEAD}),
    )
    db = FakeDb([CURRENT_HEAD])

    result = health.ready(db)

    assert result["status"] == "ready"
    assert result["migrations"] == "current"
    assert db.rollbacks == 0


def test_not_ready_when_database_revision_is_old(monkeypatch):
    monkeypatch.setattr(
        health,
        "_expected_migration_heads",
        lambda: frozenset({CURRENT_HEAD}),
    )
    db = FakeDb(["0016_one_active_cart"])

    with pytest.raises(HTTPException) as exc_info:
        health.ready(db)

    assert exc_info.value.status_code == 503
    assert db.rollbacks == 1


def test_not_ready_when_migration_graph_is_invalid(monkeypatch):
    def fail():
        raise RuntimeError("no head")

    monkeypatch.setattr(health, "_expected_migration_heads", fail)
    db = FakeDb([CURRENT_HEAD])

    with pytest.raises(HTTPException) as exc_info:
        health.ready(db)

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail == "Service is not ready"
