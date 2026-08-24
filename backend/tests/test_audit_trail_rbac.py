from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from backend.api import platform as platform_api
from backend.models import AdminRolePermission, AuditTrail
from backend.services.rbac import DEFAULT_PERMISSIONS


class QueryStub:
    def __init__(self, rows):
        self.rows = list(rows)

    def filter(self, *args, **kwargs):
        return self

    def order_by(self, *args, **kwargs):
        return self

    def limit(self, *args, **kwargs):
        return self

    def all(self):
        return list(self.rows)


class AuditDb:
    def __init__(self, permission_rows=None, audit_rows=None):
        self.permission_rows = list(permission_rows or [])
        self.audit_rows = list(audit_rows or [])
        self.audit_queries = 0

    def query(self, model):
        if model is AdminRolePermission:
            return QueryStub(self.permission_rows)
        if model is AuditTrail:
            self.audit_queries += 1
            return QueryStub(self.audit_rows)
        raise AssertionError(f"Unexpected query model: {model}")


def _admin(role, admin_id=1):
    return SimpleNamespace(id=admin_id, role=role, email=f"{role}@flashin.test")


def test_default_manager_order_and_security_read_do_not_grant_cross_domain_audit():
    assert "orders.read" in DEFAULT_PERMISSIONS["manager"]
    assert "security.read" in DEFAULT_PERMISSIONS["manager"]
    assert "audit.read" not in DEFAULT_PERMISSIONS["manager"]

    db = AuditDb()

    with pytest.raises(HTTPException) as exc:
        platform_api.list_audit_trail(admin=_admin("manager", 11), db=db)

    assert exc.value.status_code == 403
    assert exc.value.detail == "Missing permission: audit.read"
    assert db.audit_queries == 0, "Unauthorized readers must be rejected before audit data is queried"


def test_owner_wildcard_can_read_audit_trail():
    row = SimpleNamespace(id=7, action="platform.remote_config.upsert")
    db = AuditDb(audit_rows=[row])

    result = platform_api.list_audit_trail(admin=_admin("owner"), db=db)

    assert result == [row]
    assert db.audit_queries == 1


def test_custom_role_requires_explicit_audit_read_grant():
    row = SimpleNamespace(id=8, action="business_event.replay")
    db = AuditDb(
        permission_rows=[SimpleNamespace(permission="audit.read")],
        audit_rows=[row],
    )

    result = platform_api.list_audit_trail(admin=_admin("compliance", 22), db=db)

    assert result == [row]
    assert db.audit_queries == 1
