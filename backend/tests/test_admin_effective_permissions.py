from pathlib import Path
from types import SimpleNamespace

from backend.services.rbac import DEFAULT_PERMISSIONS, effective_permissions, has_permission


class _Query:
    def __init__(self, rows):
        self.rows = rows

    def filter(self, *_args, **_kwargs):
        return self

    def all(self):
        return self.rows


class _Db:
    def __init__(self, permissions=()):
        self.permissions = list(permissions)
        self.query_calls = 0

    def query(self, _model):
        self.query_calls += 1
        return _Query([SimpleNamespace(permission=value) for value in self.permissions])


def test_owner_effective_permissions_are_unrestricted_without_db_lookup():
    db = _Db(["orders.read"])
    admin = SimpleNamespace(role="owner")

    assert effective_permissions(db, admin) == {"*"}
    assert has_permission(db, admin, "anything.at.all") is True
    assert db.query_calls == 0


def test_default_role_permissions_are_used_when_no_override_rows_exist():
    db = _Db()
    admin = SimpleNamespace(role="warehouse")

    assert effective_permissions(db, admin) == DEFAULT_PERMISSIONS["warehouse"]
    assert has_permission(db, admin, "inventory.write") is True
    assert has_permission(db, admin, "products.write") is False


def test_configured_role_rows_replace_defaults_exactly():
    db = _Db(["orders.read", " custom.permission ", ""])
    admin = SimpleNamespace(role="manager")

    assert effective_permissions(db, admin) == {"orders.read", "custom.permission"}
    assert has_permission(db, admin, "orders.read") is True
    assert has_permission(db, admin, "products.read") is False


def test_unknown_role_without_configuration_has_no_permissions():
    db = _Db()
    admin = SimpleNamespace(role="unknown-role")

    assert effective_permissions(db, admin) == set()
    assert has_permission(db, admin, "orders.read") is False


def test_admin_session_endpoint_is_authenticated_no_store_and_sanitized():
    source = (
        Path(__file__).resolve().parents[1] / "api" / "admin_auth.py"
    ).read_text(encoding="utf-8")

    assert '@router.get("/session")' in source
    assert "Depends(get_current_admin)" in source
    assert 'response.headers["Cache-Control"] = "no-store, max-age=0"' in source
    assert 'response.headers["Pragma"] = "no-cache"' in source
    assert '"all_access": "*" in permissions' in source
    assert '"permissions": sorted(' in source
    endpoint = source.split('@router.get("/session")', 1)[1].split('@router.post("/logout"', 1)[0]
    for forbidden in ("password_hash", "totp", "session_hash", "ip_address", "access_token"):
        assert forbidden not in endpoint
