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
            "backend",
            "frontend",
            "admin",
            "bot",
            "meilisearch",
            *worker_names,
        }
    }
    services["backend"].update(
        {
            "healthcheck": {"test": ["CMD-SHELL", "curl -fsS http://localhost:8000/ready"]},
            "depends_on": {"db": {"condition": "service_healthy"}},
            "volumes": [
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

    services["prometheus"] = {
        "restart": "unless-stopped",
        "image": "prom/prometheus:v3.5.0",
        "healthcheck": {"test": ["CMD-SHELL", "wget -qO- http://localhost:9090/-/ready"]},
        "depends_on": {"backend": {"condition": "service_healthy"}},
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
    return {"services": services}


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
