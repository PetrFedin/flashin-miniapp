from backend.config import Settings
from backend.services import diagnostics


CURRENT_HEAD = "0042_moysklad_stock_evidence_concurrency"


class _ScalarResult:
    def __init__(self, values=None):
        self._values = list(values or [])

    def scalars(self):
        return self

    def all(self):
        return self._values


class _Query:
    def join(self, *_args, **_kwargs):
        return self

    def filter(self, *_args, **_kwargs):
        return self

    def order_by(self, *_args, **_kwargs):
        return self

    def count(self):
        return 0

    def first(self):
        return None


class _Db:
    def execute(self, statement):
        if "version_num" in str(statement):
            return _ScalarResult([CURRENT_HEAD])
        return _ScalarResult()

    def query(self, *_args, **_kwargs):
        return _Query()


def _disabled_production_settings() -> Settings:
    return Settings(
        _env_file=None,
        app_env="production",
        database_url="postgresql+psycopg2://flashin:strong-password@db:5432/flashin",
        cors_origins="https://mini.flashin.store,https://admin.flashin.store",
        telegram_bot_token="1234567890:abcdefghijklmnopqrstuvwxyz",
        jwt_secret="j" * 48,
        admin_email="admin@flashin.store",
        admin_password="",
        admin_totp_encryption_key="t" * 48,
        outbox_signing_secret="o" * 48,
        commercial_checkout_enabled=False,
        payments_mode="disabled",
        payment_provider="yookassa",
        yookassa_shop_id="",
        yookassa_secret_key="",
        pilot_runtime_enforced=False,
        pilot_evidence_signing_secret="",
        moysklad_mode="disabled",
        moysklad_order_export_enabled=False,
        moysklad_token="",
        moysklad_login="",
        moysklad_password="",
        meilisearch_enabled=False,
        scheduler_enabled=True,
        media_storage="local",
        enable_seed=False,
        use_create_all=False,
    )


def test_provider_disabled_production_is_healthy_not_degraded(monkeypatch):
    settings = _disabled_production_settings()
    monkeypatch.setattr(diagnostics, "get_settings", lambda: settings)
    monkeypatch.setattr(diagnostics, "migrations_are_current", lambda _db: True)

    report = diagnostics.run_diagnostics(_Db())

    assert report["checks"]["env"]["ok"] is True
    assert "admin_password" not in report["checks"]["env"]["missing_or_default"]
    assert report["checks"]["payments"] == {
        "ok": True,
        "status": "disabled",
        "mode": "disabled",
        "provider": None,
    }
    assert report["checks"]["moysklad"]["ok"] is True
    assert report["checks"]["moysklad"]["status"] == "disabled"
    assert report["checks"]["moysklad_sync"]["ok"] is True
    assert report["checks"]["moysklad_sync"]["latest_status"] == "disabled"
    assert report["checks"]["search"] == {
        "ok": True,
        "enabled": False,
        "mode": "database",
    }
    assert report["ok"] is True
