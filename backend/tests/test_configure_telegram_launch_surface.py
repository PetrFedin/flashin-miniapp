from __future__ import annotations

import importlib.util
import io
import json
from pathlib import Path
import sys
import urllib.error

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"


def _load():
    spec = importlib.util.spec_from_file_location(
        "flashin_configure_telegram_launch_surface",
        SCRIPTS / "configure_telegram_launch_surface.py",
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class _Response:
    def __init__(self, payload):
        self._raw = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return self._raw


def _env(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456789:private-bot-token")
    monkeypatch.setenv("MINI_APP_URL", "https://mini.flashin.store/")
    monkeypatch.setenv("TELEGRAM_MENU_BUTTON_TEXT", "Open FLASHIN")


def _menu(url="https://mini.flashin.store", text="Open FLASHIN"):
    return {
        "type": "web_app",
        "text": text,
        "web_app": {"url": url},
    }


def test_already_correct_configuration_is_read_only(monkeypatch, capsys):
    module = _load()
    _env(monkeypatch)
    requests = []

    def success(request, timeout=0):
        requests.append(request)
        assert request.method == "GET"
        assert request.full_url.endswith("/getChatMenuButton")
        return _Response({"ok": True, "result": _menu()})

    monkeypatch.setattr(module.HTTP_OPENER, "open", success)
    assert module.main([]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload == {
        "status": "ok",
        "provider": "telegram",
        "changed": False,
        "read_back_verified": True,
    }
    assert len(requests) == 1


def test_drift_requires_explicit_provider_change_acknowledgement(monkeypatch, capsys):
    module = _load()
    _env(monkeypatch)
    requests = []

    def current(request, timeout=0):
        requests.append(request)
        return _Response({"ok": True, "result": {"type": "commands"}})

    monkeypatch.setattr(module.HTTP_OPENER, "open", current)
    assert module.main([]) == 1
    output = capsys.readouterr().out
    assert "provider change is required" in output
    assert "private-bot-token" not in output
    assert "mini.flashin.store" not in output
    assert len(requests) == 1
    assert requests[0].method == "GET"


def test_acknowledged_drift_is_changed_then_read_back(monkeypatch, capsys):
    module = _load()
    _env(monkeypatch)
    requests = []

    def provider(request, timeout=0):
        requests.append(request)
        if len(requests) == 1:
            assert request.method == "GET"
            assert request.full_url.endswith("/getChatMenuButton")
            return _Response({"ok": True, "result": {"type": "commands"}})
        if len(requests) == 2:
            assert request.method == "POST"
            assert request.full_url.endswith("/setChatMenuButton")
            body = json.loads(request.data.decode("utf-8"))
            assert body == {"menu_button": _menu(url="https://mini.flashin.store/")}
            return _Response({"ok": True, "result": True})
        assert len(requests) == 3
        assert request.method == "GET"
        assert request.full_url.endswith("/getChatMenuButton")
        return _Response({"ok": True, "result": _menu()})

    monkeypatch.setattr(module.HTTP_OPENER, "open", provider)
    assert module.main(["--acknowledge-provider-change"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload == {
        "status": "ok",
        "provider": "telegram",
        "changed": True,
        "read_back_verified": True,
    }
    assert len(requests) == 3


def test_read_back_mismatch_fails_closed_without_leaking_remote_url(monkeypatch, capsys):
    module = _load()
    _env(monkeypatch)
    requests = []
    remote_url = "https://wrong-private.example.test/path"

    def provider(request, timeout=0):
        requests.append(request)
        if len(requests) == 1:
            return _Response({"ok": True, "result": {"type": "commands"}})
        if len(requests) == 2:
            return _Response({"ok": True, "result": True})
        return _Response({"ok": True, "result": _menu(url=remote_url)})

    monkeypatch.setattr(module.HTTP_OPENER, "open", provider)
    assert module.main(["--acknowledge-provider-change"]) == 1
    output = capsys.readouterr().out
    assert "read-back does not match" in output
    assert remote_url not in output
    assert "private-bot-token" not in output
    assert "mini.flashin.store" not in output
    assert len(requests) == 3


def test_http_failure_output_is_bounded(monkeypatch, capsys):
    module = _load()
    _env(monkeypatch)
    provider_body = b'{"description":"private provider detail"}'

    def fail(request, timeout=0):
        raise urllib.error.HTTPError(
            request.full_url,
            400,
            "bad request private detail",
            {},
            io.BytesIO(provider_body),
        )

    monkeypatch.setattr(module.HTTP_OPENER, "open", fail)
    assert module.main([]) == 1
    output = capsys.readouterr().out
    assert "HTTP 400" in output
    assert "private-bot-token" not in output
    assert "private provider detail" not in output
    assert "api.telegram.org" not in output
    assert "mini.flashin.store" not in output
