from pathlib import Path
from types import SimpleNamespace

from backend.services.rbac import DEFAULT_PERMISSIONS, has_permission


def _platform_source() -> str:
    return (
        Path(__file__).resolve().parents[1] / "api" / "platform.py"
    ).read_text(encoding="utf-8")


def _route_block(source: str, marker: str, next_marker: str) -> str:
    return source.split(marker, 1)[1].split(next_marker, 1)[0]


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


def test_business_event_read_endpoints_require_events_read_not_orders_read():
    source = _platform_source()
    assert '_EVENT_READ_PERMISSION = "events.read"' in source

    blocks = (
        _route_block(
            source,
            '@router.get("/admin/events/summary")',
            '@router.get("/admin/events")',
        ),
        _route_block(
            source,
            '@router.get("/admin/events")',
            '@router.get("/admin/events/{event_id}")',
        ),
        _route_block(
            source,
            '@router.get("/admin/events/{event_id}")',
            '@router.post("/admin/events/{event_id}/replay")',
        ),
    )

    for block in blocks:
        assert "require_permission(db, admin, _EVENT_READ_PERMISSION)" in block
        assert 'require_permission(db, admin, "orders.read")' not in block


def test_business_event_replay_remains_independent_events_replay_permission():
    source = _platform_source()
    block = _route_block(
        source,
        '@router.post("/admin/events/{event_id}/replay")',
        '@router.get("/admin/audit-trail", response_model=list[AuditTrailOut])',
    )

    assert '_EVENT_REPLAY_PERMISSION = "events.replay"' in source
    assert "require_permission(db, admin, _EVENT_REPLAY_PERMISSION)" in block
    assert "require_permission(db, admin, _EVENT_READ_PERMISSION)" not in block
    assert 'require_permission(db, admin, "orders.write")' not in block


def test_default_operational_roles_do_not_receive_event_diagnostics_or_replay():
    for role in ("manager", "support", "warehouse"):
        assert "events.read" not in DEFAULT_PERMISSIONS[role]
        assert "events.replay" not in DEFAULT_PERMISSIONS[role]


def test_owner_wildcard_and_explicit_custom_event_permissions_remain_supported():
    owner = SimpleNamespace(role="owner")
    assert has_permission(_Db(), owner, "events.read") is True
    assert has_permission(_Db(), owner, "events.replay") is True

    technical_reader = SimpleNamespace(role="technical-reader")
    read_db = _Db(["events.read"])
    assert has_permission(read_db, technical_reader, "events.read") is True
    assert has_permission(read_db, technical_reader, "events.replay") is False

    replay_operator = SimpleNamespace(role="technical-replay")
    replay_db = _Db(["events.replay"])
    assert has_permission(replay_db, replay_operator, "events.read") is False
    assert has_permission(replay_db, replay_operator, "events.replay") is True
