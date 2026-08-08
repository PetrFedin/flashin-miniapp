from scripts.provider_wiring_preflight import validate_wiring


def valid_env() -> dict[str, str]:
    return {
        "APP_ENV": "production",
        "MINI_APP_URL": "https://mini.flashin.store",
        "API_PUBLIC_URL": "https://api.flashin.store",
        "ADMIN_URL": "https://admin.flashin.store",
        "YOOKASSA_RETURN_URL": "https://mini.flashin.store/payment-result",
        "YOOKASSA_WEBHOOK_URL": "https://api.flashin.store/api/webhooks/yookassa",
        "TELEGRAM_BOT_TOKEN": "telegram-token",
        "BOT_TOKEN": "telegram-token",
        "YOOKASSA_SHOP_ID": "shop-1",
        "YOOKASSA_SECRET_KEY": "secret-1",
        "MOYSKLAD_TOKEN": "moy-token",
        "MOYSKLAD_ORDER_EXPORT_ENABLED": "true",
        "MOYSKLAD_ORGANIZATION_ID": "org-1",
        "MOYSKLAD_AGENT_ID": "agent-1",
        "MOYSKLAD_STORE_ID": "store-1",
        "SCHEDULER_ENABLED": "true",
        "PILOT_RUNTIME_ENFORCED": "true",
    }


def test_provider_wiring_preflight_accepts_coherent_pilot_config():
    report = validate_wiring(valid_env())
    assert report["go"] is True
    assert report["summary"]["failed"] == 0


def test_provider_wiring_preflight_rejects_wrong_yookassa_callback_and_disabled_workers():
    env = valid_env()
    env["YOOKASSA_WEBHOOK_URL"] = "https://api.flashin.store/api/returns/webhook/yookassa"
    env["SCHEDULER_ENABLED"] = "false"

    report = validate_wiring(env)
    assert report["go"] is False
    failed = {item["name"] for item in report["checks"] if not item["ok"]}
    assert "yookassa_canonical_webhook" in failed
    assert "scheduler_enabled" in failed


def test_provider_wiring_preflight_rejects_mismatched_telegram_aliases():
    env = valid_env()
    env["BOT_TOKEN"] = "other-token"

    report = validate_wiring(env)
    failed = {item["name"] for item in report["checks"] if not item["ok"]}
    assert "telegram_token_alias_consistency" in failed
