from pathlib import Path
from types import SimpleNamespace

from backend.services.rbac import (
    DEFAULT_PERMISSIONS,
    REFUNDS_WRITE_PERMISSION,
    has_permission,
)


def _returns_source() -> str:
    return (
        Path(__file__).resolve().parents[1] / "api" / "returns.py"
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


def test_refund_provider_mutation_requires_dedicated_financial_permission_before_state_access():
    source = _returns_source()
    marker = '@router.post("/admin/approve")'
    block = source.split(marker, 1)[1]

    assert "REFUNDS_WRITE_PERMISSION" in block
    assert 'require_permission(db, admin, "orders.write")' not in block

    permission_check = block.index("require_permission(db, admin, REFUNDS_WRITE_PERMISSION)")
    first_database_access = block.index("db.query(")
    first_provider_call = min(
        block.index("fetch_yookassa_refund("),
        block.index("create_yookassa_refund("),
    )
    assert permission_check < first_database_access < first_provider_call


def test_operational_default_roles_do_not_inherit_refund_financial_authority():
    assert REFUNDS_WRITE_PERMISSION == "refunds.write"
    for role in ("manager", "support", "warehouse"):
        assert REFUNDS_WRITE_PERMISSION not in DEFAULT_PERMISSIONS[role]


def test_owner_and_explicit_finance_role_can_receive_refund_authority_independently():
    owner = SimpleNamespace(role="owner")
    assert has_permission(_Db(), owner, REFUNDS_WRITE_PERMISSION) is True

    order_manager = SimpleNamespace(role="order-manager")
    orders_db = _Db(["orders.read", "orders.write"])
    assert has_permission(orders_db, order_manager, "orders.write") is True
    assert has_permission(orders_db, order_manager, REFUNDS_WRITE_PERMISSION) is False

    finance_operator = SimpleNamespace(role="finance-operator")
    refunds_db = _Db(["orders.read", REFUNDS_WRITE_PERMISSION])
    assert has_permission(refunds_db, finance_operator, "orders.write") is False
    assert has_permission(refunds_db, finance_operator, REFUNDS_WRITE_PERMISSION) is True
