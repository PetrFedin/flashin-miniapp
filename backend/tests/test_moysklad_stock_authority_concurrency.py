from sqlalchemy import create_engine, inspect
from sqlalchemy.exc import IntegrityError

from backend.database import Base
from backend.services.moysklad_stock_authority import _is_open_evidence_unique_race


def test_create_all_mirrors_moysklad_open_evidence_unique_indexes():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    inspector = inspect(engine)

    conflict_indexes = {
        row["name"]: row
        for row in inspector.get_indexes("moysklad_conflicts")
    }
    reconciliation_indexes = {
        row["name"]: row
        for row in inspector.get_indexes("stock_reconciliation_logs")
    }

    conflict = conflict_indexes[
        "uq_moysklad_conflict_open_stale_physical_return"
    ]
    reconciliation = reconciliation_indexes[
        "uq_stock_reconciliation_open_blocked_physical_return"
    ]
    assert conflict["unique"] == 1
    assert reconciliation["unique"] == 1


class _Diagnostic:
    def __init__(self, constraint_name: str | None):
        self.constraint_name = constraint_name


class _OriginalError(Exception):
    def __init__(self, constraint_name: str | None, message: str = ""):
        super().__init__(message)
        self.diag = _Diagnostic(constraint_name)


def _integrity_error(constraint_name: str | None, message: str = "") -> IntegrityError:
    return IntegrityError(
        "statement",
        {},
        _OriginalError(constraint_name, message),
    )


def test_only_owned_open_evidence_unique_races_are_retryable():
    assert _is_open_evidence_unique_race(
        _integrity_error("uq_moysklad_conflict_open_stale_physical_return")
    )
    assert _is_open_evidence_unique_race(
        _integrity_error("uq_stock_reconciliation_open_blocked_physical_return")
    )

    assert not _is_open_evidence_unique_race(
        _integrity_error("uq_return_logistics_case_idempotency")
    )
    assert not _is_open_evidence_unique_race(
        _integrity_error(None, "duplicate provider command")
    )


def test_sqlite_owned_unique_messages_are_retryable_without_masking_others():
    assert _is_open_evidence_unique_race(
        _integrity_error(
            None,
            "UNIQUE constraint failed: "
            "moysklad_conflicts.moysklad_id, "
            "moysklad_conflicts.conflict_type",
        )
    )
    assert _is_open_evidence_unique_race(
        _integrity_error(
            None,
            "UNIQUE constraint failed: stock_reconciliation_logs.variant_id",
        )
    )
    assert not _is_open_evidence_unique_race(
        _integrity_error(
            None,
            "UNIQUE constraint failed: provider_commands.idempotency_key",
        )
    )
