from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from backend.api.payment_reconciliation import _provider_payment_snapshot
from backend.services.rbac import (
    DEFAULT_PERMISSIONS,
    PAYMENT_RECONCILIATION_READ_PERMISSION,
    PAYMENT_RECONCILIATION_WRITE_PERMISSION,
    has_permission,
)


def _source() -> str:
    return (
        Path(__file__).resolve().parents[1] / "api" / "payment_reconciliation.py"
    ).read_text(encoding="utf-8")


class _Query:
    def __init__(self, permissions):
        self.permissions = list(permissions)

    def filter(self, *_args, **_kwargs):
        return self

    def all(self):
        return [SimpleNamespace(permission=value) for value in self.permissions]


class _Db:
    def __init__(self, permissions=()):
        self.permissions = permissions

    def query(self, _model):
        return _Query(self.permissions)


def test_payment_reconciliation_routes_use_dedicated_financial_permissions():
    source = _source()
    list_block = source.split('@router.get("", response_model=list[PaymentReconciliationOut])', 1)[1].split(
        '@router.post("/payments/{payment_id}/check"', 1
    )[0]
    check_block = source.split('@router.post("/payments/{payment_id}/check"', 1)[1].split(
        '@router.post("/{row_id}/resolve")', 1
    )[0]
    resolve_block = source.split('@router.post("/{row_id}/resolve")', 1)[1]

    assert "PAYMENT_RECONCILIATION_READ_PERMISSION" in list_block
    assert 'require_permission(db, admin, "orders.read")' not in list_block
    assert "PAYMENT_RECONCILIATION_WRITE_PERMISSION" in check_block
    assert 'require_permission(db, admin, "orders.write")' not in check_block
    assert "PAYMENT_RECONCILIATION_WRITE_PERMISSION" in resolve_block
    assert 'require_permission(db, admin, "orders.write")' not in resolve_block


def test_reconciliation_write_permission_is_checked_before_payment_or_provider_access():
    source = _source()
    block = source.split('@router.post("/payments/{payment_id}/check"', 1)[1].split(
        '@router.post("/{row_id}/resolve")', 1
    )[0]

    permission_check = block.index(
        "require_permission(db, admin, PAYMENT_RECONCILIATION_WRITE_PERMISSION)"
    )
    payment_query = block.index("db.query(Payment)")
    provider_call = block.index("fetch_yookassa_payment(")
    reconciliation_write = block.index("create_reconciliation_row(")
    audit_write = block.index('"payment.reconciliation.check"')

    assert permission_check < payment_query < provider_call < reconciliation_write < audit_write
    assert "db.flush()" in block
    assert "provider_payment_id" not in block.split('"payment.reconciliation.check"', 1)[1]


def test_resolve_is_row_locked_idempotent_and_audited_once():
    source = _source()
    block = source.split('@router.post("/{row_id}/resolve")', 1)[1]

    assert ".with_for_update()" in block
    assert 'if row.status == "resolved":' in block
    idempotent_return = block.index('return {"ok": True, "idempotent": True}')
    mutation = block.index("resolve_reconciliation(row, message)")
    audit = block.index('"payment.reconciliation.resolve"')
    commit = block.index("db.commit()")
    assert idempotent_return < mutation < audit < commit
    assert "stop_pilot" not in block


def test_operational_default_roles_do_not_inherit_reconciliation_authority():
    assert PAYMENT_RECONCILIATION_READ_PERMISSION == "payments.reconciliation.read"
    assert PAYMENT_RECONCILIATION_WRITE_PERMISSION == "payments.reconciliation.write"
    for role in ("manager", "support", "warehouse"):
        assert PAYMENT_RECONCILIATION_READ_PERMISSION not in DEFAULT_PERMISSIONS[role]
        assert PAYMENT_RECONCILIATION_WRITE_PERMISSION not in DEFAULT_PERMISSIONS[role]


def test_owner_and_explicit_finance_role_can_receive_reconciliation_without_order_write():
    owner = SimpleNamespace(role="owner")
    assert has_permission(_Db(), owner, PAYMENT_RECONCILIATION_READ_PERMISSION) is True
    assert has_permission(_Db(), owner, PAYMENT_RECONCILIATION_WRITE_PERMISSION) is True

    order_manager = SimpleNamespace(role="order-manager")
    orders_db = _Db(["orders.read", "orders.write"])
    assert has_permission(orders_db, order_manager, "orders.write") is True
    assert has_permission(orders_db, order_manager, PAYMENT_RECONCILIATION_READ_PERMISSION) is False
    assert has_permission(orders_db, order_manager, PAYMENT_RECONCILIATION_WRITE_PERMISSION) is False

    finance_operator = SimpleNamespace(role="finance-operator")
    finance_db = _Db(
        [
            PAYMENT_RECONCILIATION_READ_PERMISSION,
            PAYMENT_RECONCILIATION_WRITE_PERMISSION,
        ]
    )
    assert has_permission(finance_db, finance_operator, "orders.write") is False
    assert has_permission(finance_db, finance_operator, PAYMENT_RECONCILIATION_READ_PERMISSION) is True
    assert has_permission(finance_db, finance_operator, PAYMENT_RECONCILIATION_WRITE_PERMISSION) is True


def test_provider_snapshot_rejects_malformed_or_incomplete_financial_data():
    for payload in (None, [], {}, {"status": "succeeded"}, {"amount": {"value": "10.00"}}):
        with pytest.raises(HTTPException) as error:
            _provider_payment_snapshot(payload)
        assert error.value.status_code == 502


def test_provider_snapshot_keeps_amount_uncoerced_for_decimal_validation():
    status, amount = _provider_payment_snapshot(
        {"status": "succeeded", "amount": {"value": "1700.00", "currency": "RUB"}}
    )

    assert status == "succeeded"
    assert amount == "1700.00"
