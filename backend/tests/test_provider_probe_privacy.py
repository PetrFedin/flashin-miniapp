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
    mini_app_url = "https://private-mini.example.test"
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", token)
    monkeypatch.setenv("MINI_APP_URL", mini_app_url)

    def fail(request, timeout=0):
        raise urllib.error.URLError(f"request failed for {request.full_url}")

    monkeypatch.setattr(module.urllib.request, "urlopen", fail)
    assert module.main() == 1
    failed_output = capsys.readouterr().out
    assert token not in failed_output
    assert mini_app_url not in failed_output
    assert "api.telegram.org" not in failed_output
    assert "request failed" not in failed_output

    def success(request, timeout=0):
        if request.full_url.endswith("/getMe"):
            return _Response(
                {
                    "ok": True,
                    "result": {
                        "id": 99887766,
                        "username": "private_bot_username",
                        "can_join_groups": True,
                        "has_main_web_app": True,
                    },
                }
            )
        if request.full_url.endswith("/getChatMenuButton"):
            return _Response(
                {
                    "ok": True,
                    "result": {
                        "type": "web_app",
                        "text": "Open FLASHIN",
                        "web_app": {"url": mini_app_url},
                    },
                }
            )
        raise AssertionError("unexpected Telegram Bot API method")

    monkeypatch.setattr(module.urllib.request, "urlopen", success)
    assert module.main() == 0
    success_output = capsys.readouterr().out
    payload = json.loads(success_output)
    assert payload == {
        "status": "ok",
        "provider": "telegram",
        "identity_verified": True,
        "menu_button_verified": True,
        "launch_url_verified": True,
        "main_web_app_configured": True,
    }
    assert token not in success_output
    assert mini_app_url not in success_output
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
    secret = "meili-private-master-key"
    index = "private-products-index"
    url = "https://meili.private.invalid"
    monkeypatch.setenv("MEILISEARCH_URL", url)
    monkeypatch.setenv("MEILISEARCH_MASTER_KEY", secret)
    monkeypatch.setenv("MEILISEARCH_PRODUCTS_INDEX", index)

    class _FailingClient:
        def health(self):
            raise RuntimeError(f"private failure url={url} key={secret} index={index}")

    class _MeiliModule:
        @staticmethod
        def Client(_url, _key):
            return _FailingClient()

    monkeypatch.setitem(sys.modules, "meilisearch", _MeiliModule())
    assert module.main() == 1
    output = capsys.readouterr().out
    assert secret not in output
    assert url not in output
    assert index not in output
    assert "private failure" not in output


def test_moysklad_probe_does_not_print_credentials_or_provider_body(monkeypatch, capsys):
    module = _load_script("check_moysklad.py", "flashin_check_moysklad_privacy")
    token = "moysklad-private-token"
    login = "private-login"
    password = "private-password"
    monkeypatch.setenv("MOYSKLAD_TOKEN", token)
    monkeypatch.setenv("MOYSKLAD_LOGIN", login)
    monkeypatch.setenv("MOYSKLAD_PASSWORD", password)

    provider_body = b'{"errors":[{"error":"provider-private-moysklad-body"}]}'

    def http_fail(request, timeout=0):
        raise urllib.error.HTTPError(
            request.full_url,
            401,
            "unauthorized",
            {},
            io.BytesIO(provider_body),
        )

    monkeypatch.setattr(module.urllib.request, "urlopen", http_fail)
    assert module.main() == 1
    output = capsys.readouterr().out
    assert token not in output
    assert login not in output
    assert password not in output
    assert "provider-private-moysklad-body" not in output


def test_r2_probe_does_not_print_secret_endpoint_bucket_or_exception(monkeypatch, capsys):
    module = _load_script("check_r2_s3.py", "flashin_check_r2_privacy")
    secret = "r2-private-secret"
    endpoint = "https://account-private.r2.cloudflarestorage.com"
    bucket = "private-bucket-name"
    monkeypatch.setenv("S3_ENDPOINT_URL", endpoint)
    monkeypatch.setenv("S3_BUCKET", bucket)
    monkeypatch.setenv("S3_ACCESS_KEY_ID", "private-access-key")
    monkeypatch.setenv("S3_SECRET_ACCESS_KEY", secret)

    class _FailingS3:
        def head_bucket(self, **_kwargs):
            raise RuntimeError(
                f"private failure endpoint={endpoint} bucket={bucket} secret={secret}"
            )

    class _Boto3:
        @staticmethod
        def client(*_args, **_kwargs):
            return _FailingS3()

    monkeypatch.setitem(sys.modules, "boto3", _Boto3())
    assert module.main() == 1
    output = capsys.readouterr().out
    assert secret not in output
    assert endpoint not in output
    assert bucket not in output
    assert "private failure" not in output
