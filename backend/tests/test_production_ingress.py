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
            "TELEGRAM_BOT_TOKEN": "test-bot-token-" + "x" * 32,
            "JWT_SECRET": "j" * 48,
            "ADMIN_EMAIL": "admin@test.local",
            "ADMIN_PASSWORD": "Test-Admin-Password-2026",
            "ADMIN_TOTP_ENCRYPTION_KEY": "t" * 48,
            "OUTBOX_SIGNING_SECRET": "o" * 48,
            "DATABASE_URL": "postgresql+psycopg2://app:test-db-password@db:5432/flashin",
            "CORS_ORIGINS": "https://mini.test.local,https://admin.test.local",
            "MINI_APP_URL": "https://mini.test.local",
            "API_PUBLIC_URL": "https://api.test.local",
            "PAYMENT_PROVIDER": "yookassa",
            "YOOKASSA_SHOP_ID": "test-shop",
            "YOOKASSA_SECRET_KEY": "test-payment-credential",
            "YOOKASSA_RETURN_URL": "https://mini.test.local/payment-result",
            "ENABLE_SEED": "false",
            "USE_CREATE_ALL": "false",
            "METRICS_ENABLED": "false",
        }
    )
    code = """
from backend.main import app
assert app.docs_url is None
assert app.redoc_url is None
assert app.openapi_url is None
paths = {route.path for route in app.routes}
assert '/metrics' not in paths
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
