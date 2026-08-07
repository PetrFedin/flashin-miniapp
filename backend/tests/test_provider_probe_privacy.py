from __future__ import annotations

import base64
import importlib.util
import io
import json
from pathlib import Path
import sys
import urllib.error
import urllib.parse

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


def test_telegram_probe_never_prints_token_url_or_identity(monkeypatch, capsys):
    module = _load_script("check_telegram_bot.py", "flashin_check_telegram_privacy")
    token = "123456789:telegram-secret-token"
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", token)

    def fail(request, timeout=0):
        raise urllib.error.URLError(f"request failed for {request.full_url}")

    monkeypatch.setattr(module.urllib.request, "urlopen", fail)
    assert module.main() == 1
    failed_output = capsys.readouterr().out
    assert token not in failed_output
    assert "api.telegram.org" not in failed_output
    assert "request failed" not in failed_output

    def success(_request, timeout=0):
        return _Response(
            {
                "ok": True,
                "result": {
                    "id": 99887766,
                    "username": "private_bot_username",
                    "can_join_groups": True,
                },
            }
        )

    monkeypatch.setattr(module.urllib.request, "urlopen", success)
    assert module.main() == 0
    success_output = capsys.readouterr().out
    payload = json.loads(success_output)
    assert payload == {
        "status": "ok",
        "provider": "telegram",
        "identity_verified": True,
    }
    assert "99887766" not in success_output
    assert "private_bot_username" not in success_output


def test_yookassa_probe_never_prints_provider_body_credentials_or_payment_id(monkeypatch, capsys):
    module = _load_script("check_yookassa_test.py", "flashin_check_yookassa_privacy")
    secret = "yookassa-secret-value"
    monkeypatch.setenv("YOOKASSA_SHOP_ID", "shop-123")
    monkeypatch.setenv("YOOKASSA_SECRET_KEY", secret)
    monkeypatch.setenv("YOOKASSA_RETURN_URL", "https://mini.flashin.test/payment-result")
    monkeypatch.setenv("FLASHIN_RELEASE_GIT_COMMIT", "abc123")

    provider_body = b'{"description":"provider-private-error-body"}'

    def http_fail(request, timeout=0):
        raise urllib.error.HTTPError(
            request.full_url,
            400,
            "bad request",
            {},
            io.BytesIO(provider_body),
        )

    monkeypatch.setattr(module.urllib.request, "urlopen", http_fail)
    assert module.main() == 1
    failed_output = capsys.readouterr().out
    assert secret not in failed_output
    assert "provider-private-error-body" not in failed_output
    assert "detail" not in failed_output

    payment_id = "2f4b-private-provider-payment-id"
    confirmation_url = "https://yookassa.example/confirmation/private-token"

    def success(_request, timeout=0):
        return _Response(
            {
                "id": payment_id,
                "status": "pending",
                "amount": {"value": "1.00", "currency": "RUB"},
                "confirmation": {"confirmation_url": confirmation_url},
            }
        )

    monkeypatch.setattr(module.urllib.request, "urlopen", success)
    assert module.main() == 0
    success_output = capsys.readouterr().out
    payload = json.loads(success_output)
    assert payload["ok"] is True
    assert payload["provider"] == "yookassa"
    assert "payment_id" not in payload
    assert payment_id not in success_output
    assert confirmation_url not in success_output


def test_meilisearch_probe_does_not_print_exception_url_key_or_index(monkeypatch, capsys):
    module = _load_script("check_meilisearch.py", "flashin_check_meilisearch_privacy")
    base_url = "https://search.private.flashin.test"
    master_key = "meili-master-secret"
    private_index = "private-products-index"
    monkeypatch.setenv("MEILISEARCH_URL", base_url)
    monkeypatch.setenv("MEILISEARCH_MASTER_KEY", master_key)
    monkeypatch.setenv("MEILISEARCH_PRODUCTS_INDEX", private_index)

    def fail(request, timeout=0):
        raise urllib.error.URLError(f"failed URL {request.full_url} with key {master_key}")

    monkeypatch.setattr(module.urllib.request, "urlopen", fail)
    assert module.main() == 1
    failed_output = capsys.readouterr().out
    assert base_url not in failed_output
    assert master_key not in failed_output
    assert private_index not in failed_output
    assert "failed URL" not in failed_output


def test_r2_probe_suppresses_provider_exception_details_and_attempts_cleanup(monkeypatch, capsys):
    module = _load_script("check_r2_s3.py", "flashin_check_r2_privacy")
    monkeypatch.setenv("S3_ENDPOINT_URL", "https://private-r2.example")
    monkeypatch.setenv("S3_BUCKET", "private-bucket")
    monkeypatch.setenv("S3_ACCESS_KEY_ID", "private-access-key")
    monkeypatch.setenv("S3_SECRET_ACCESS_KEY", "private-secret-key")

    class Client:
        delete_calls = 0

        def put_object(self, **_kwargs):
            return None

        def get_object(self, **_kwargs):
            raise RuntimeError("private-bucket private-secret-key https://private-r2.example")

        def delete_object(self, **_kwargs):
            self.delete_calls += 1

    client = Client()

    class Session:
        def client(self, *_args, **_kwargs):
            return client

    monkeypatch.setattr(module.boto3.session, "Session", Session)
    assert module.main() == 1
    output = capsys.readouterr().out
    assert "RuntimeError" in output
    assert "private-bucket" not in output
    assert "private-secret-key" not in output
    assert "private-r2.example" not in output
    assert client.delete_calls == 1


def test_integration_evidence_redacts_encoded_and_basic_auth_forms_and_suppresses_runner_errors(
    monkeypatch,
):
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    module = _load_script("check_integrations.py", "flashin_check_integrations_privacy")
    env = {
        "TELEGRAM_BOT_TOKEN": "123456:telegram-secret",
        "YOOKASSA_SHOP_ID": "shop-123",
        "YOOKASSA_SECRET_KEY": "yookassa-secret",
        "MOYSKLAD_LOGIN": "operator@example.test",
        "MOYSKLAD_PASSWORD": "moy-password",
        "PILOT_EVIDENCE_SIGNING_SECRET": "evidence-signing-secret",
    }
    encoded_token = urllib.parse.quote(env["TELEGRAM_BOT_TOKEN"], safe="")
    yookassa_basic = base64.b64encode(
        f"{env['YOOKASSA_SHOP_ID']}:{env['YOOKASSA_SECRET_KEY']}".encode()
    ).decode()
    moysklad_basic = base64.b64encode(
        f"{env['MOYSKLAD_LOGIN']}:{env['MOYSKLAD_PASSWORD']}".encode()
    ).decode()
    text = (
        f"raw={env['YOOKASSA_SECRET_KEY']} encoded={encoded_token} "
        f"telegram=https://api.telegram.org/bot{env['TELEGRAM_BOT_TOKEN']}/getMe "
        f"yk={yookassa_basic} moy={moysklad_basic}"
    )
    redacted = module.redact(text, env)
    assert "telegram-secret" not in redacted
    assert "yookassa-secret" not in redacted
    assert yookassa_basic not in redacted
    assert moysklad_basic not in redacted
    assert "api.telegram.org/bot123456" not in redacted

    def fail_runner(*_args, **_kwargs):
        raise OSError("https://api.telegram.org/bot123456:telegram-secret/getMe")

    result = module.run_probe(
        module.Probe("telegram", "check_telegram_bot.py", 30),
        env=env,
        host_python=True,
        runner=fail_runner,
    )
    assert result == {
        "name": "telegram",
        "ok": False,
        "returncode": None,
        "stdout": "",
        "stderr": "OSError",
    }
