from scripts.pilot_evidence import CONFIG_FINGERPRINT_KEYS, configuration_fingerprint


CRITICAL_WIRING_KEYS = (
    "COMMERCIAL_CHECKOUT_ENABLED",
    "PAYMENTS_MODE",
    "YOOKASSA_WEBHOOK_URL",
    "MOYSKLAD_MODE",
    "MOYSKLAD_ORDER_EXPORT_ENABLED",
    "MOYSKLAD_ORGANIZATION_ID",
    "MOYSKLAD_AGENT_ID",
    "MOYSKLAD_STORE_ID",
    "MOYSKLAD_DELIVERY_SERVICE_ID",
    "SCHEDULER_ENABLED",
    "PROVIDER_COMMAND_POLL_SECONDS",
    "NOTIFICATION_MAX_ATTEMPTS",
    "NOTIFICATION_LEASE_SECONDS",
    "RATE_LIMIT_ENABLED",
    "RATE_LIMIT_BACKEND",
    "RATE_LIMIT_REDIS_URL",
    "RATE_LIMIT_REDIS_CONNECT_TIMEOUT_SECONDS",
    "RATE_LIMIT_REDIS_SOCKET_TIMEOUT_SECONDS",
    "RATE_LIMIT_PER_MINUTE",
    "RATE_LIMIT_AUTH_PER_MINUTE",
    "RATE_LIMIT_ADMIN_LOGIN_PER_MINUTE",
    "RATE_LIMIT_SEARCH_PER_MINUTE",
    "RATE_LIMIT_CHECKOUT_PER_MINUTE",
    "RATE_LIMIT_PAYMENT_PER_MINUTE",
    "RATE_LIMIT_RETURN_PER_MINUTE",
    "RATE_LIMIT_SUPPORT_PER_MINUTE",
    "RATE_LIMIT_WEBHOOK_PER_MINUTE",
    "PILOT_RUNTIME_ENFORCED",
    "PILOT_RUNTIME_MAX_ORDERS",
)


def base_env() -> dict[str, str]:
    return {key: f"value:{key}" for key in CONFIG_FINGERPRINT_KEYS}


def test_configuration_fingerprint_binds_provider_and_runtime_wiring():
    secret = "configuration-fingerprint-test-secret-0123456789"
    env = base_env()
    baseline = configuration_fingerprint(env, secret)

    for key in CRITICAL_WIRING_KEYS:
        assert key in CONFIG_FINGERPRINT_KEYS
        mutated = dict(env)
        mutated[key] = mutated[key] + ":changed"
        assert configuration_fingerprint(mutated, secret) != baseline, key
