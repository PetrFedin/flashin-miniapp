from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest

from backend.api import crm as crm_api
from backend.models import CrmProfile, Customer, Order
from backend.services import crm as crm_service
from backend.services.rbac import CRM_RECOMPUTE_PERMISSION, DEFAULT_PERMISSIONS


class QueryStub:
    def __init__(self, session, entity):
        self.session = session
        self.entity = entity

    def filter(self, *args, **kwargs):
        return self

    def order_by(self, *args, **kwargs):
        return self

    def limit(self, *args, **kwargs):
        return self

    def all(self):
        if self.entity is Order:
            return list(self.session.orders)
        if self.entity is Customer:
            return list(self.session.customers)
        return []

    def first(self):
        if self.entity is CrmProfile:
            return self.session.profile
        return None


class SessionStub:
    def __init__(self, *, orders=None, customers=None, profile=None):
        self.orders = list(orders or [])
        self.customers = list(customers or [])
        self.profile = profile
        self.added = []
        self.commits = 0
        self.rollbacks = 0

    def query(self, entity):
        return QueryStub(self, entity)

    def add(self, value):
        self.added.append(value)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


def test_default_operational_roles_do_not_inherit_crm_recompute_authority():
    assert CRM_RECOMPUTE_PERMISSION == "crm.recompute"
    assert CRM_RECOMPUTE_PERMISSION not in DEFAULT_PERMISSIONS["manager"]
    assert CRM_RECOMPUTE_PERMISSION not in DEFAULT_PERMISSIONS["support"]
    assert CRM_RECOMPUTE_PERMISSION not in DEFAULT_PERMISSIONS["warehouse"]
    assert "customers.read" in DEFAULT_PERMISSIONS["support"]


def test_recompute_customer_profile_preserves_ledger_owned_loyalty_balance(monkeypatch):
    fixed_now = datetime(2026, 9, 4, 12, 0, 0)
    monkeypatch.setattr(crm_service, "utcnow_naive", lambda: fixed_now)

    profile = SimpleNamespace(
        customer_id=7,
        segment="old",
        orders_count=0,
        total_spent=Decimal("0.00"),
        average_order_value=Decimal("0.00"),
        last_order_at=None,
        loyalty_points=Decimal("137.5000"),
        vip=False,
        updated_at=None,
    )
    orders = [
        SimpleNamespace(
            payment_status="paid",
            total_amount=Decimal("1200.50"),
            created_at=datetime(2026, 8, 1, 10, 0, 0),
        ),
        SimpleNamespace(
            payment_status="paid",
            total_amount=Decimal("799.50"),
            created_at=datetime(2026, 8, 3, 11, 0, 0),
        ),
        SimpleNamespace(
            payment_status="refunded",
            total_amount=Decimal("5000.00"),
            created_at=datetime(2026, 8, 4, 11, 0, 0),
        ),
    ]
    db = SessionStub(orders=orders, profile=profile)

    result = crm_service.recompute_customer_profile(db, 7)

    assert result is profile
    assert profile.segment == "repeat"
    assert profile.orders_count == 2
    assert profile.total_spent == Decimal("2000.00")
    assert profile.average_order_value == Decimal("1000.00")
    assert profile.last_order_at == datetime(2026, 8, 3, 11, 0, 0)
    assert profile.vip is False
    assert profile.updated_at == fixed_now
    assert profile.loyalty_points == Decimal("137.5000")
    assert db.commits == 0


def test_recompute_all_profiles_stages_changes_without_committing(monkeypatch):
    db = SessionStub(customers=[SimpleNamespace(id=1), SimpleNamespace(id=2)])
    visited = []

    def fake_recompute(_db, customer_id):
        assert _db is db
        visited.append(customer_id)
        return SimpleNamespace(customer_id=customer_id)

    monkeypatch.setattr(crm_service, "recompute_customer_profile", fake_recompute)

    assert crm_service.recompute_all_profiles(db) == 2
    assert visited == [1, 2]
    assert db.commits == 0


def test_recompute_endpoint_requires_maintenance_permission_and_commits_with_audit(monkeypatch):
    db = SessionStub()
    admin = SimpleNamespace(id=91, role="maintenance")
    permissions = []
    audits = []

    def fake_require(_db, _admin, permission):
        assert _db is db
        assert _admin is admin
        permissions.append(permission)

    def fake_audit(_db, _admin, action, entity_type="", entity_id="", payload=None):
        assert _db is db
        assert _admin is admin
        audits.append((action, entity_type, entity_id, payload))

    monkeypatch.setattr(crm_api, "require_permission", fake_require)
    monkeypatch.setattr(crm_api, "recompute_all_profiles", lambda _db: 4)
    monkeypatch.setattr(crm_api, "log_admin_action", fake_audit)

    assert crm_api.recompute(admin=admin, db=db) == {"ok": True, "profiles": 4}
    assert permissions == [CRM_RECOMPUTE_PERMISSION]
    assert audits == [
        ("crm.profiles.recompute", "crm_profile", "", {"profiles": 4})
    ]
    assert db.commits == 1
    assert db.rollbacks == 0


def test_recompute_endpoint_rolls_back_if_audit_or_staged_write_fails(monkeypatch):
    db = SessionStub()
    admin = SimpleNamespace(id=91, role="maintenance")

    monkeypatch.setattr(crm_api, "require_permission", lambda *args, **kwargs: None)
    monkeypatch.setattr(crm_api, "recompute_all_profiles", lambda _db: 4)

    def fail_audit(*args, **kwargs):
        raise RuntimeError("audit unavailable")

    monkeypatch.setattr(crm_api, "log_admin_action", fail_audit)

    with pytest.raises(RuntimeError, match="audit unavailable"):
        crm_api.recompute(admin=admin, db=db)

    assert db.commits == 0
    assert db.rollbacks == 1
