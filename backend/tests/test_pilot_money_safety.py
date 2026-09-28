from backend.services.pilot_money_safety import build_pilot_money_safety


class _Query:
    def __init__(self, session):
        self.session = session

    def outerjoin(self, *args, **kwargs):
        return self

    def filter(self, *args, **kwargs):
        return self

    def scalar(self):
        return self.session.scalars.pop(0)


class _Session:
    def __init__(self, scalars):
        self.scalars = list(scalars)
        self.query_count = 0

    def query(self, *args, **kwargs):
        self.query_count += 1
        return _Query(self)


def test_empty_pilot_scope_is_healthy_without_database_queries():
    db = _Session([])
    result = build_pilot_money_safety(db, [])
    assert result == {
        "healthy": True,
        "attention_required": False,
        "payment_review_orders": 0,
        "refund_attention_orders": 0,
        "reconciliation_mismatches": 0,
        "blocking_codes": [],
        "stop_reason": None,
    }
    assert db.query_count == 0


def test_payment_review_is_a_fail_closed_money_blocker():
    db = _Session([1, 0, 0, 0])
    result = build_pilot_money_safety(db, [42, 42, -1, "bad"])
    assert result["healthy"] is False
    assert result["attention_required"] is True
    assert result["payment_review_orders"] == 1
    assert result["blocking_codes"] == ["pilot_payment_review_required"]
    assert result["stop_reason"] == "payment_review_required"
    assert db.query_count == 4


def test_reconciliation_mismatch_has_deterministic_stop_priority():
    db = _Session([0, 2, 1, 2])
    result = build_pilot_money_safety(db, [7])
    assert result["healthy"] is False
    assert result["refund_attention_orders"] == 2
    assert result["reconciliation_mismatches"] == 2
    assert result["blocking_codes"] == [
        "pilot_refund_attention_required",
        "pilot_payment_reconciliation_mismatch",
    ]
    assert result["stop_reason"] == "payment_reconciliation_mismatch"


def test_money_safety_contract_exposes_only_counts_and_bounded_codes():
    db = _Session([0, 1, 1, 0])
    result = build_pilot_money_safety(db, [9])
    serialized = repr(result)
    assert result["stop_reason"] == "refund_review_required"
    assert set(result) == {
        "healthy",
        "attention_required",
        "payment_review_orders",
        "refund_attention_orders",
        "reconciliation_mismatches",
        "blocking_codes",
        "stop_reason",
    }
    for forbidden in ("payload", "last_error", "idempotency", "telegram", "payment_id"):
        assert forbidden not in serialized.lower()
