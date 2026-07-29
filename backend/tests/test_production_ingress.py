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
            "TELEGRAM_BOT_TOKEN": "test-token",
            "JWT_SECRET": "test-secret",
            "ADMIN_EMAIL": "admin@test.local",
            "ADMIN_PASSWORD": "test-password",
            "DATABASE_URL": "postgresql+psycopg2://flashin:flashin@db:5432/flashin",
            "MINI_APP_URL": "https://mini.flashin.store",
            "API_PUBLIC_URL": "https://api.flashin.store",
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
