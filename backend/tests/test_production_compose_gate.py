import importlib.util
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "check_production_compose.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("check_production_compose", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _safe_config() -> dict:
    worker_names = {
        "notification_worker",
        "ops_jobs",
        "outbox_jobs",
        "provider_command_jobs",
        "moysklad_sync",
        "campaign_jobs",
        "sla_jobs",
        "event_jobs",
        "media_jobs",
        "scheduler",
    }
    services = {
        name: {"restart": "unless-stopped"}
        for name in {
            "db",
            "redis",
            "backend",
            "frontend",
            "admin",
            "bot",
            "meilisearch",
            *worker_names,
        }
    }
    app_runtime_names = {"backend", "bot", *worker_names}
    for name in app_runtime_names:
        services[name].update(
            {
                "user": "10001:10001",
                "read_only": True,
                "cap_drop": ["ALL"],
                "security_opt": ["no-new-privileges:true"],
                "tmpfs": ["/tmp:rw,nosuid,nodev,size=64m"],
            }
        )
    services["redis"].update(
        {
            "image": "redis:8.10.1-alpine",
            "command": [
                "redis-server",
                "--appendonly",
                "yes",
                "--appendfsync",
                "everysec",
                "--save",
                "",
            ],
            "healthcheck": {"test": ["CMD", "redis-cli", "ping"]},
            "volumes": [
                {
                    "type": "volume",
                    "source": "rate_limit_redis",
                    "target": "/data",
                }
            ],
        }
    )
    services["backend"].update(
        {
            "healthcheck": {"test": ["CMD-SHELL", "curl -fsS http://localhost:8000/ready"]},
            "depends_on": {
                "db": {"condition": "service_healthy"},
                "redis": {"condition": "service_healthy"},
            },
            "volumes": [
                {"type": "volume", "source": "media", "target": "/app/media"},
                {"type": "volume", "source": "exports_data", "target": "/app/exports"},
                {"type": "bind", "source": "./docs", "target": "/app/docs", "read_only": True},
                {
                    "type": "bind",
                    "source": "./deploy/release",
                    "target": "/app/deploy/release",
                    "read_only": True,
                },
            ],
        }
    )
    for worker_name in worker_names:
        services[worker_name]["depends_on"] = {"db": {"condition": "service_healthy"}}
    services["media_jobs"]["volumes"] = [
        {"type": "volume", "source": "media", "target": "/app/media"}
    ]

    services["alertmanager"] = {
        "restart": "unless-stopped",
        "image": "prom/alertmanager:v0.33.1",
        "command": [
            "--config.file=/run/secrets/alertmanager.yml",
            "--storage.path=/alertmanager",
        ],
        "healthcheck": {"test": ["CMD-SHELL", "wget -qO- http://localhost:9093/-/ready"]},
        "secrets": [
            {"source": "alertmanager_config", "target": "alertmanager.yml"},
        ],
        "volumes": [
            {
                "type": "volume",
                "source": "alertmanager_data",
                "target": "/alertmanager",
            }
        ],
    }
    services["prometheus"] = {
        "restart": "unless-stopped",
        "image": "prom/prometheus:v3.5.0",
        "healthcheck": {"test": ["CMD-SHELL", "wget -qO- http://localhost:9090/-/ready"]},
        "depends_on": {
            "backend": {"condition": "service_healthy"},
            "alertmanager": {"condition": "service_healthy"},
        },
        "volumes": [
            {
                "type": "bind",
                "source": "./deploy/monitoring/prometheus.yml",
                "target": "/etc/prometheus/prometheus.yml",
                "read_only": True,
            },
            {
                "type": "bind",
                "source": "./deploy/monitoring/rules",
                "target": "/etc/prometheus/rules",
                "read_only": True,
            },
        ],
    }
    services["grafana"] = {
        "restart": "unless-stopped",
        "image": "grafana/grafana:12.1.0",
        "healthcheck": {"test": ["CMD-SHELL", "wget -qO- http://localhost:3000/api/health"]},
        "depends_on": {"prometheus": {"condition": "service_healthy"}},
        "environment": {
            "GF_AUTH_ANONYMOUS_ENABLED": "false",
            "GF_USERS_ALLOW_SIGN_UP": "false",
            "GF_SECURITY_ADMIN_USER": "pilot-operator",
            "GF_SECURITY_ADMIN_PASSWORD": "non-default-test-password",
        },
    }
    services["caddy"] = {
        "restart": "unless-stopped",
        "ports": [
            {"target": 80, "published": "80", "protocol": "tcp"},
            {"target": 443, "published": "443", "protocol": "tcp"},
        ],
        "depends_on": {
            "frontend": {"condition": "service_started"},
            "admin": {"condition": "service_started"},
            "backend": {"condition": "service_started"},
        },
    }
    return {
        "services": services,
        "secrets": {
            "alertmanager_config": {
                "file": "./deploy/runtime/alertmanager.yml",
            }
        },
    }


def test_safe_production_config_passes():
    module = _load_module()

    assert module.validate_config(_safe_config()) == []


def test_internal_service_port_is_rejected():
    module = _load_module()
    config = _safe_config()
    config["services"]["backend"]["ports"] = [
        {"target": 8000, "published": "8000", "protocol": "tcp"}
    ]

    errors = module.validate_config(config)

    assert any("Internal service backend publishes host ports" in error for error in errors)


def test_host_network_mode_is_rejected():
    module = _load_module()
    config = _safe_config()
    config["services"]["db"]["network_mode"] = "host"

    errors = module.validate_config(config)

    assert any("db must not use host network mode" in error for error in errors)


def test_caddy_must_publish_exact_public_ports():
    module = _load_module()
    config = _safe_config()
    config["services"]["caddy"]["ports"] = [
        {"target": 80, "published": "8080", "protocol": "tcp"}
    ]

    errors = module.validate_config(config)

    assert any("Caddy must publish only" in error for error in errors)


def test_redis_rate_limit_authority_requires_persistence_and_backend_dependency():
    module = _load_module()
    config = _safe_config()
    config["services"]["redis"]["command"] = ["redis-server"]
    config["services"]["redis"]["volumes"] = []
    config["services"]["backend"]["depends_on"].pop("redis")

    errors = module.validate_config(config)

    assert any("Redis rate-limit service is missing persistence option" in error for error in errors)
    assert "Redis rate-limit service must use durable /data storage" in errors
    assert "Backend must wait for healthy Redis rate-limit authority" in errors


def test_redis_rate_limit_authority_must_not_publish_host_port():
    module = _load_module()
    config = _safe_config()
    config["services"]["redis"]["ports"] = [
        {"target": 6379, "published": "6379", "protocol": "tcp"}
    ]

    errors = module.validate_config(config)

    assert any("Internal service redis publishes host ports" in error for error in errors)


def test_backend_healthcheck_must_use_readiness_endpoint():
    module = _load_module()
    config = _safe_config()
    config["services"]["backend"]["healthcheck"]["test"] = [
        "CMD-SHELL",
        "curl -fsS http://localhost:8000/health",
    ]

    errors = module.validate_config(config)

    assert "Backend healthcheck must use /ready, not only liveness" in errors


def test_required_service_restart_policy_is_enforced():
    module = _load_module()
    config = _safe_config()
    config["services"]["scheduler"].pop("restart")

    errors = module.validate_config(config)

    assert "Service scheduler must use restart: unless-stopped" in errors



def test_application_runtimes_require_least_privilege_contract():
    module = _load_module()
    config = _safe_config()
    config["services"]["backend"].update({"user": "0:0", "read_only": False, "cap_drop": [], "security_opt": [], "tmpfs": []})

    errors = module.validate_config(config)

    assert "backend must run as 10001:10001" in errors
    assert "backend must use a read-only root filesystem" in errors
    assert "backend must drop all Linux capabilities" in errors
    assert "backend must enforce no-new-privileges" in errors
    assert "backend must mount writable /tmp tmpfs" in errors


def test_application_runtime_rejects_unexpected_writable_app_mount():
    module = _load_module()
    config = _safe_config()
    config["services"]["ops_jobs"]["volumes"] = [
        {"type": "volume", "source": "unexpected_cache", "target": "/app/cache"}
    ]

    errors = module.validate_config(config)

    assert "ops_jobs has unexpected writable application mount: /app/cache" in errors


def test_required_writable_application_volume_cannot_disappear():
    module = _load_module()
    config = _safe_config()
    config["services"]["backend"]["volumes"] = [
        mount
        for mount in config["services"]["backend"]["volumes"]
        if mount.get("target") != "/app/exports"
    ]

    errors = module.validate_config(config)

    assert "backend must mount writable /app/exports" in errors

def test_alertmanager_must_use_runtime_secret_and_non_public_storage():
    module = _load_module()
    config = _safe_config()
    config["secrets"].pop("alertmanager_config")
    config["services"]["alertmanager"]["secrets"] = []
    config["services"]["alertmanager"]["volumes"] = [
        {
            "type": "bind",
            "source": "./alertmanager-data",
            "target": "/alertmanager",
        }
    ]

    errors = module.validate_config(config)

    assert "Alertmanager must consume the rendered config as a Compose secret" in errors
    assert "Compose must define the alertmanager_config secret" in errors
    assert "Alertmanager /alertmanager storage must be a named volume" in errors


def test_prometheus_must_wait_for_alertmanager_health():
    module = _load_module()
    config = _safe_config()
    config["services"]["prometheus"]["depends_on"].pop("alertmanager")

    errors = module.validate_config(config)

    assert "Prometheus must wait for healthy Alertmanager" in errors
