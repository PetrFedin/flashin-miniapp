import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_production_fastapi_disables_documentation_routes():
    env = os.environ.copy()
    env.update(
        {
            "APP_ENV": "production",
            "TELEGRAM_BOT_TOKEN": "1234567890:production-test-token-value",
            "JWT_SECRET": "j" * 48,
            "ADMIN_EMAIL": "admin@test.local",
            "ADMIN_TOTP_ENCRYPTION_KEY": "t" * 48,
            "OUTBOX_SIGNING_SECRET": "o" * 48,
            "PILOT_EVIDENCE_SIGNING_SECRET": "p" * 48,
            "PILOT_RUNTIME_ENFORCED": "true",
            "PILOT_RUNTIME_MAX_ORDERS": "20",
            "DATABASE_URL": "postgresql+psycopg2://flashin_ci:strong-ci-password@db:5432/flashin",
            "CORS_ORIGINS": "https://mini.flashin.store,https://admin.flashin.store",
            "MINI_APP_URL": "https://mini.flashin.store",
            "API_PUBLIC_URL": "https://api.flashin.store",
            "PAYMENT_PROVIDER": "yookassa",
            "YOOKASSA_SHOP_ID": "production-test-shop",
            "YOOKASSA_SECRET_KEY": "production-test-secret",
            "YOOKASSA_RETURN_URL": "https://mini.flashin.store/payment-result",
            "ENABLE_SEED": "false",
            "USE_CREATE_ALL": "false",
            "METRICS_ENABLED": "false",
        }
    )
    # The backend CI job intentionally carries a development ADMIN_PASSWORD.
    # A production subprocess must prove it can start only after that value is removed.
    for key in (
        "ADMIN_PASSWORD",
        "MOYSKLAD_TOKEN",
        "MOYSKLAD_LOGIN",
        "MOYSKLAD_PASSWORD",
    ):
        env.pop(key, None)

    code = """
from starlette.routing import NoMatchFound
from backend.main import app
assert app.docs_url is None
assert app.redoc_url is None
assert app.openapi_url is None
try:
    app.url_path_for('metrics')
except NoMatchFound:
    pass
else:
    raise AssertionError('/metrics must not be registered when metrics are disabled')
"""

    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_caddy_publishes_one_authoritative_client_ip_to_backend():
    caddyfile = (ROOT / "deploy" / "Caddyfile").read_text(encoding="utf-8")

    assert "header_up X-Forwarded-For {remote_host}" in caddyfile
    assert "header_up -X-Real-IP" in caddyfile
