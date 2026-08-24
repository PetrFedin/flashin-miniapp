import json
from types import SimpleNamespace

from backend.api import platform as platform_api
from backend.models import RemoteConfig


class RemoteConfigQueryStub:
    def __init__(self, rows):
        self.rows = list(rows)
        self.filters = []

    def filter(self, *criteria):
        self.filters.extend(criteria)
        return self

    def all(self):
        return list(self.rows)


class RemoteConfigDbStub:
    def __init__(self, rows):
        self.query_stub = RemoteConfigQueryStub(rows)

    def query(self, model):
        assert model is RemoteConfig
        return self.query_stub


def _row(key, value):
    return SimpleNamespace(key=key, value_json=json.dumps(value, ensure_ascii=False))


def test_public_remote_config_only_exposes_explicit_public_namespace():
    db = RemoteConfigDbStub(
        [
            _row("public.checkout", {"support_phone": "+70000000000"}),
            _row("payments.provider", {"private_token": "must-never-leak"}),
            _row("telegram.bot", {"token": "must-never-leak"}),
        ]
    )

    result = platform_api.public_remote_config(db=db)

    assert result == {
        "public.checkout": {"support_phone": "+70000000000"},
    }
    assert db.query_stub.filters, "Public config lookup must be filtered at the database boundary"
    assert "must-never-leak" not in json.dumps(result, ensure_ascii=False)


def test_public_remote_config_fails_closed_for_invalid_or_non_object_values():
    db = RemoteConfigDbStub(
        [
            SimpleNamespace(key="public.invalid", value_json="not-json"),
            SimpleNamespace(key="public.list", value_json="[1, 2, 3]"),
            _row("private.valid", {"secret": "hidden"}),
        ]
    )

    assert platform_api.public_remote_config(db=db) == {}


def test_public_remote_config_namespace_is_explicit_and_stable():
    assert platform_api._PUBLIC_REMOTE_CONFIG_PREFIX == "public."
    assert platform_api._is_public_remote_config_key("public.checkout") is True
    assert platform_api._is_public_remote_config_key("public.") is False
    assert platform_api._is_public_remote_config_key("payments.provider") is False
    assert platform_api._is_public_remote_config_key(" public.checkout") is False
