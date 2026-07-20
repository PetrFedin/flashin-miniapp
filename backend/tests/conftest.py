import os
import sys
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test-token")
os.environ.setdefault("JWT_SECRET", "test-secret")
os.environ.setdefault("ADMIN_EMAIL", "admin@test.local")
os.environ.setdefault("ADMIN_PASSWORD", "test-password")
os.environ.setdefault("DATABASE_URL", "sqlite://")
os.environ.setdefault("MINI_APP_URL", "http://localhost:5173")
os.environ.setdefault("API_PUBLIC_URL", "http://localhost:8000")

# Tests must not inherit unrelated or legacy keys from a developer's local .env.
from backend.config import Settings

Settings.model_config["env_file"] = None
