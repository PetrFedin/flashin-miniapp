import os

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test-token")
os.environ.setdefault("BOT_TOKEN", "test-token")
os.environ.setdefault("JWT_SECRET", "test-secret")
os.environ.setdefault("ADMIN_EMAIL", "admin@test.local")
os.environ.setdefault("ADMIN_PASSWORD", "test-password")
os.environ.setdefault("DATABASE_URL", "postgresql+psycopg2://flashin:flashin@db:5432/flashin")
os.environ.setdefault("MINI_APP_URL", "http://localhost:5173")
os.environ.setdefault("API_PUBLIC_URL", "http://localhost:8000")
os.environ.setdefault("ADMIN_URL", "http://localhost:5174")
