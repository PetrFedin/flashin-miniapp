from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import stat
import sys
import urllib.error

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"


def _load_script(filename: str, module_name: str):
    spec = importlib.util.spec_from_file_location(module_name, SCRIPTS / filename)
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


def test_requires_explicit_acknowledgement_before_network(monkeypatch, capsys):
    module = _load_script("telegram_real_auth_smoke.py", "flashin_live_auth_ack")
    monkeypatch.setenv("TELEGRAM_INIT_DATA", "private-init-data")
    monkeypatch.setenv("TELEGRAM_EXPECTED_USER_ID", "123456789")
    monkeypatch.setenv("API_PUBLIC_URL", "https://api.flashin.store")
    monkeypatch.setattr(module.HTTP_OPENER, "open", lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("network")))

    assert module.main([]) == 1
    output = capsys.readouterr().out
    assert "acknowledgement" in output
    assert "private-init-data" not in output
    assert "123456789" not in output


def test_rejects_non_https_api_base(monkeypatch, capsys):
    module = _load_script("telegram_real_auth_smoke.py", "flashin_live_auth_https")
    monkeypatch.setenv("TELEGRAM_INIT_DATA", "private-init-data")
    monkeypatch.setenv("TELEGRAM_EXPECTED_USER_ID", "123456789")
    monkeypatch.setenv("API_PUBLIC_URL", "http://api.flashin.store")

    assert module.main(["--acknowledge-customer-provisioning"]) == 1
    output = capsys.readouterr().out
    assert "must use HTTPS" in output
    assert "private-init-data" not in output
    assert "123456789" not in output


def test_live_auth_smoke_writes_only_sanitized_private_evidence(monkeypatch, tmp_path, capsys):
    module = _load_script("telegram_real_auth_smoke.py", "flashin_live_auth_success")
    init_data = "query_id=private&user=%7B%22id%22%3A123456789%7D&auth_date=1&hash=privatehash"
    token = "customer-private-jwt"
    user_id = "123456789"
    monkeypatch.setenv("TELEGRAM_INIT_DATA", init_data)
    monkeypatch.setenv("TELEGRAM_EXPECTED_USER_ID", user_id)
    monkeypatch.setenv("API_PUBLIC_URL", "https://API.flashin.store/")
    module.ROOT = tmp_path
    module.EVIDENCE_DIR = tmp_path / "docs" / "pilot" / "evidence"

    seen = []

    def success(request, timeout=0):
        seen.append(request)
        if request.full_url.endswith("/api/auth/telegram"):
            assert request.method == "POST"
            assert json.loads(request.data)["init_data"] == init_data
            return _Response({"access_token": token, "token_type": "bearer"})
        if request.full_url.endswith("/api/auth/me"):
            assert request.get_header("Authorization") == f"Bearer {token}"
            return _Response({"id": 1, "telegram_id": user_id, "username": "private-user"})
        raise AssertionError("unexpected URL")

    monkeypatch.setattr(module.HTTP_OPENER, "open", success)
    assert module.main(["--acknowledge-customer-provisioning"]) == 0
    output = capsys.readouterr().out
    result = json.loads(output)
    assert result["status"] == "PASS"
    assert result["scenario"] == "telegram_real_auth"
    evidence_path = tmp_path / result["evidence_path"]
    assert evidence_path.is_file()
    payload = json.loads(evidence_path.read_text(encoding="utf-8"))
    assert payload["signed_init_data_accepted"] is True
    assert payload["customer_session_verified"] is True
    assert payload["expected_identity_verified"] is True
    assert init_data not in evidence_path.read_text(encoding="utf-8")
    assert token not in evidence_path.read_text(encoding="utf-8")
    assert user_id not in evidence_path.read_text(encoding="utf-8")
    assert "private-user" not in evidence_path.read_text(encoding="utf-8")
    assert init_data not in output
    assert token not in output
    assert user_id not in output
    assert "private-user" not in output
    assert len(result["evidence_sha256"]) == 64
    if os.name == "posix":
        assert stat.S_IMODE(evidence_path.stat().st_mode) == 0o600
        assert stat.S_IMODE(evidence_path.parent.stat().st_mode) == 0o700
    assert len(seen) == 2


def test_identity_mismatch_fails_without_leaking_identifiers(monkeypatch, tmp_path, capsys):
    module = _load_script("telegram_real_auth_smoke.py", "flashin_live_auth_mismatch")
    init_data = "private-init-data"
    token = "customer-private-jwt"
    expected_id = "123456789"
    remote_id = "987654321"
    monkeypatch.setenv("TELEGRAM_INIT_DATA", init_data)
    monkeypatch.setenv("TELEGRAM_EXPECTED_USER_ID", expected_id)
    monkeypatch.setenv("API_PUBLIC_URL", "https://api.flashin.store")
    module.ROOT = tmp_path
    module.EVIDENCE_DIR = tmp_path / "docs" / "pilot" / "evidence"

    def success(request, timeout=0):
        if request.full_url.endswith("/api/auth/telegram"):
            return _Response({"access_token": token})
        return _Response({"id": 1, "telegram_id": remote_id, "username": "private-user"})

    monkeypatch.setattr(module.HTTP_OPENER, "open", success)
    assert module.main(["--acknowledge-customer-provisioning"]) == 1
    output = capsys.readouterr().out
    assert "does not match expected pilot identity" in output
    for secret in (init_data, token, expected_id, remote_id, "private-user"):
        assert secret not in output
    assert not module.EVIDENCE_DIR.exists()


def test_http_failure_does_not_print_init_data_url_or_provider_body(monkeypatch, capsys):
    module = _load_script("telegram_real_auth_smoke.py", "flashin_live_auth_privacy")
    init_data = "private-init-data"
    expected_id = "123456789"
    api_url = "https://api.flashin.store"
    monkeypatch.setenv("TELEGRAM_INIT_DATA", init_data)
    monkeypatch.setenv("TELEGRAM_EXPECTED_USER_ID", expected_id)
    monkeypatch.setenv("API_PUBLIC_URL", api_url)

    def fail(request, timeout=0):
        raise urllib.error.HTTPError(
            request.full_url,
            401,
            "unauthorized-private-detail",
            {},
            None,
        )

    monkeypatch.setattr(module.HTTP_OPENER, "open", fail)
    assert module.main(["--acknowledge-customer-provisioning"]) == 1
    output = capsys.readouterr().out
    assert "HTTP 401" in output
    for secret in (init_data, expected_id, api_url, "unauthorized-private-detail"):
        assert secret not in output
