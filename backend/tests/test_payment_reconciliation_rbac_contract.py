from pathlib import Path
from types import SimpleNamespace

from backend.services.rbac import (
    DEFAULT_PERMISSIONS,
    PAYMENT_RECONCILIATION_READ_PERMISSION,
    PAYMENT_RECONCILIATION_WRITE_PERMISSION,
    has_permission,
)


API_SOURCE = (
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


def test_reconciliation_read_is_not_implied_by_order_visibility():
    list_block = API_SOURCE.split('@router.get("", response_model=list[PaymentReconciliationOut])', 1)[1].split(
        '@router.post("/payments/{payment_id}/check"', 1
    )[0]

    assert "PAYMENT_RECONCILIATION_READ_PERMISSION" in list_block
    assert 'require_permission(db, admin, "orders.read")' not in list_block


def test_provider_check_requires_financial_write_before_db_or_provider_access():
    block = API_SOURCE.split('@router.post("/payments/{payment_id}/check"', 1)[1].split(
        '@router.post("/{row_id}/resolve")', 1
    )[0]

    assert "PAYMENT_RECONCILIATION_WRITE_PERMISSION" in block
    assert 'require_permission(db, admin, "orders.write")' not in block
    permission_check = block.index(
        "require_permission(db, admin, PAYMENT_RECONCILIATION_WRITE_PERMISSION)"
    )
    assert permission_check < block.index("db.query(") < block.index("fetch_yookassa_payment(")
    assert "float(" not in block
    assert 'status_code=502' in block
    assert '"payment.reconciliation.check"' in block


def test_manual_resolution_is_locked_idempotent_audited_and_financially_scoped():
    block = API_SOURCE.split('@router.post("/{row_id}/resolve")', 1)[1]

    assert "PAYMENT_RECONCILIATION_WRITE_PERMISSION" in block
    assert 'require_permission(db, admin, "orders.write")' not in block
    assert ".with_for_update()" in block
    assert 'row.status == "resolved"' in block
    assert '"idempotent": True' in block
    assert '"payment.reconciliation.resolve"' in block
    assert 'max_length=1000' in block
    assert '"message_changed"' in block


def test_operational_default_roles_do_not_inherit_reconciliation_authority():
    assert PAYMENT_RECONCILIATION_READ_PERMISSION == "payments.reconciliation.read"
    assert PAYMENT_RECONCILIATION_WRITE_PERMISSION == "payments.reconciliation.write"
    for role in ("manager", "support", "warehouse"):
        assert PAYMENT_RECONCILIATION_READ_PERMISSION not in DEFAULT_PERMISSIONS[role]
        assert PAYMENT_RECONCILIATION_WRITE_PERMISSION not in DEFAULT_PERMISSIONS[role]


def test_finance_role_can_reconcile_without_generic_order_mutation_authority():
    owner = SimpleNamespace(role="owner")
    assert has_permission(_Db(), owner, PAYMENT_RECONCILIATION_READ_PERMISSION) is True
    assert has_permission(_Db(), owner, PAYMENT_RECONCILIATION_WRITE_PERMISSION) is True

    order_manager = SimpleNamespace(role="order-manager")
    orders_db = _Db(["orders.read", "orders.write"])
    assert has_permission(orders_db, order_manager, "orders.write") is True
    assert has_permission(
        orders_db, order_manager, PAYMENT_RECONCILIATION_READ_PERMISSION
    ) is False
    assert has_permission(
        orders_db, order_manager, PAYMENT_RECONCILIATION_WRITE_PERMISSION
    ) is False

    finance = SimpleNamespace(role="finance-operator")
    finance_db = _Db(
        [
            PAYMENT_RECONCILIATION_READ_PERMISSION,
            PAYMENT_RECONCILIATION_WRITE_PERMISSION,
        ]
    )
    assert has_permission(finance_db, finance, "orders.write") is False
    assert has_permission(finance_db, finance, PAYMENT_RECONCILIATION_READ_PERMISSION) is True
    assert has_permission(finance_db, finance, PAYMENT_RECONCILIATION_WRITE_PERMISSION) is True
