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
        name: {}
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
    services["caddy"] = {
        "ports": [
            {"target": 80, "published": "80", "protocol": "tcp"},
            {"target": 443, "published": "443", "protocol": "tcp"},
        ]
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
