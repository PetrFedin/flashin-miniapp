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
    services = {
        name: {"restart": "unless-stopped"}
        for name in {
            "db",
            "backend",
            "frontend",
            "admin",
            "bot",
            "notification_worker",
            "scheduler",
            "meilisearch",
        }
    }
    services["backend"].update(
        {
            "healthcheck": {"test": ["CMD-SHELL", "curl -fsS http://localhost:8000/ready"]},
            "depends_on": {"db": {"condition": "service_healthy"}},
        }
    )
    services["notification_worker"]["depends_on"] = {
        "db": {"condition": "service_healthy"}
    }
    services["scheduler"]["depends_on"] = {"db": {"condition": "service_healthy"}}
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
