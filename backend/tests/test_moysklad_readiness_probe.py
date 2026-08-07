from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
import urllib.error

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "check_moysklad.py"
INTEGRATION_RUNNER = ROOT / "scripts" / "check_integrations.py"

_IDS = {
    "MOYSKLAD_ORGANIZATION_ID": "11111111-1111-4111-8111-111111111111",
    "MOYSKLAD_AGENT_ID": "22222222-2222-4222-8222-222222222222",
    "MOYSKLAD_STORE_ID": "33333333-3333-4333-8333-333333333333",
    "MOYSKLAD_DELIVERY_SERVICE_ID": "44444444-4444-4444-8444-444444444444",
}
_ENTITY_TYPES = {
    _IDS["MOYSKLAD_ORGANIZATION_ID"]: "organization",
    _IDS["MOYSKLAD_AGENT_ID"]: "counterparty",
    _IDS["MOYSKLAD_STORE_ID"]: "store",
    _IDS["MOYSKLAD_DELIVERY_SERVICE_ID"]: "service",
}


class _Response:
    def __init__(self, payload):
        self._raw = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return self._raw


def _load_module():
    spec = importlib.util.spec_from_file_location("flashin_check_moysklad", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _configure(monkeypatch):
    monkeypatch.setenv("MOYSKLAD_TOKEN", "test-token-not-real")
    monkeypatch.setenv("MOYSKLAD_ORDER_EXPORT_ENABLED", "true")
    monkeypatch.setenv("MOYSKLAD_BASE_URL", "https://api.moysklad.test/api/remap/1.2")
    for key, value in _IDS.items():
        monkeypatch.setenv(key, value)


def _success_urlopen(calls):
    def fake(request, timeout=0):
        calls.append((request, timeout))
        if "/entity/product?" in request.full_url:
            return _Response({"meta": {"size": 2}, "rows": [{"id": "product-sample"}]})
        target_id = request.full_url.rsplit("/", 1)[-1]
        return _Response(
            {
                "id": target_id,
                "meta": {"type": _ENTITY_TYPES[target_id]},
                "archived": False,
            }
        )

    return fake


def test_probe_verifies_catalog_and_all_outbound_targets_with_get_only(monkeypatch, capsys):
    module = _load_module()
    _configure(monkeypatch)
    calls = []
    monkeypatch.setattr(module.urllib.request, "urlopen", _success_urlopen(calls))

    assert module.main() == 0
    output = capsys.readouterr().out.strip()
    payload = json.loads(output)

    assert len(calls) == 5
    assert all(request.get_method() == "GET" for request, _timeout in calls)
    assert all(timeout == 25 for _request, timeout in calls)
    assert payload == {
        "status": "ok",
        "catalog": "reachable",
        "outbound_targets": ["organization", "agent", "store", "delivery_service"],
        "write_operations": 0,
    }
    for target_id in _IDS.values():
        assert target_id not in output


def test_probe_fails_closed_when_outbound_export_is_disabled(monkeypatch, capsys):
    module = _load_module()
    _configure(monkeypatch)
    monkeypatch.setenv("MOYSKLAD_ORDER_EXPORT_ENABLED", "false")

    def unexpected_network(*_args, **_kwargs):
        raise AssertionError("network should not be touched")

    monkeypatch.setattr(module.urllib.request, "urlopen", unexpected_network)

    assert module.main() == 1
    output = capsys.readouterr().out
    assert "outbound export is disabled" in output
    assert all(target_id not in output for target_id in _IDS.values())


def test_probe_fails_closed_on_missing_or_malformed_target_without_leaking_values(
    monkeypatch, capsys
):
    module = _load_module()
    _configure(monkeypatch)
    monkeypatch.setenv("MOYSKLAD_STORE_ID", "definitely-not-a-provider-id")

    assert module.main() == 1
    output = capsys.readouterr().out
    assert "store target is not a valid UUID" in output
    assert "definitely-not-a-provider-id" not in output
    assert all(target_id not in output for target_id in _IDS.values())


@pytest.mark.parametrize(
    ("failure", "expected"),
    [
        ("identity", "agent target identity mismatch"),
        ("type", "agent target type mismatch"),
        ("archived", "agent target is archived"),
    ],
)
def test_probe_rejects_wrong_or_archived_target(monkeypatch, capsys, failure, expected):
    module = _load_module()
    _configure(monkeypatch)
    calls = []

    def fake(request, timeout=0):
        calls.append((request, timeout))
        if "/entity/product?" in request.full_url:
            return _Response({"meta": {"size": 2}, "rows": [{}]})
        target_id = request.full_url.rsplit("/", 1)[-1]
        entity_type = _ENTITY_TYPES[target_id]
        payload = {"id": target_id, "meta": {"type": entity_type}, "archived": False}
        if target_id == _IDS["MOYSKLAD_AGENT_ID"]:
            if failure == "identity":
                payload["id"] = _IDS["MOYSKLAD_STORE_ID"]
            elif failure == "type":
                payload["meta"]["type"] = "organization"
            else:
                payload["archived"] = True
        return _Response(payload)

    monkeypatch.setattr(module.urllib.request, "urlopen", fake)

    assert module.main() == 1
    output = capsys.readouterr().out
    assert expected in output
    assert all(target_id not in output for target_id in _IDS.values())


def test_http_failure_is_bounded_and_does_not_expose_target_url(monkeypatch, capsys):
    module = _load_module()
    _configure(monkeypatch)
    calls = []

    def fake(request, timeout=0):
        calls.append((request, timeout))
        if "/entity/product?" in request.full_url:
            return _Response({"meta": {"size": 2}, "rows": [{}]})
        raise urllib.error.HTTPError(request.full_url, 404, "not found", {}, None)

    monkeypatch.setattr(module.urllib.request, "urlopen", fake)

    assert module.main() == 1
    output = capsys.readouterr().out
    assert "organization probe returned HTTP 404" in output
    assert all(target_id not in output for target_id in _IDS.values())
    assert "api.moysklad.test" not in output


def test_live_integration_runner_keeps_moysklad_probe_required():
    source = INTEGRATION_RUNNER.read_text(encoding="utf-8")
    assert 'Probe("moysklad", "check_moysklad.py", 45)' in source
