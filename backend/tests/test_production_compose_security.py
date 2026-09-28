import json
from types import SimpleNamespace

from scripts import check_production_compose


def _safe_config():
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
            "healthcheck": {
                "test": ["CMD-SHELL", "curl -fsS http://localhost:8000/ready"]
            },
            "depends_on": {
                "db": {"condition": "service_healthy"},
                "redis": {"condition": "service_healthy"},
            },
            "volumes": [
                {"type": "volume", "source": "media", "target": "/app/media"},
                {"type": "volume", "source": "exports_data", "target": "/app/exports"},
                {
                    "type": "bind",
                    "source": "./docs",
                    "target": "/app/docs",
                    "read_only": True,
                },
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
        services[worker_name]["depends_on"] = {
            "db": {"condition": "service_healthy"}
        }
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
        "healthcheck": {
            "test": ["CMD-SHELL", "wget -qO- http://localhost:9093/-/ready"]
        },
        "secrets": [
            {"source": "alertmanager_config", "target": "alertmanager.yml"}
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
        "healthcheck": {
            "test": ["CMD-SHELL", "wget -qO- http://localhost:9090/-/ready"]
        },
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
        "healthcheck": {
            "test": ["CMD-SHELL", "wget -qO- http://localhost:3000/api/health"]
        },
        "depends_on": {"prometheus": {"condition": "service_healthy"}},
        "environment": {
            "GF_AUTH_ANONYMOUS_ENABLED": "false",
            "GF_USERS_ALLOW_SIGN_UP": "false",
            "GF_SECURITY_ADMIN_USER": "pilot-operator",
            "GF_SECURITY_ADMIN_PASSWORD": "non-default-test-password",
        },
    }
    services[check_production_compose.PUBLIC_SERVICE] = {
        "restart": "unless-stopped",
        "ports": [
            {"published": "80", "target": 80, "protocol": "tcp"},
            {"published": "443", "target": 443, "protocol": "tcp"},
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


def test_safe_production_graph_passes():
    assert check_production_compose.validate_config(_safe_config()) == []


def test_internal_host_port_is_rejected():
    config = _safe_config()
    config["services"]["backend"]["ports"] = [
        {"published": "8000", "target": 8000, "protocol": "tcp"}
    ]

    errors = check_production_compose.validate_config(config)

    assert any("Internal service backend publishes host ports" in error for error in errors)


def test_monitoring_services_must_remain_internal():
    config = _safe_config()
    config["services"]["alertmanager"]["ports"] = [
        {"published": "9093", "target": 9093, "protocol": "tcp"}
    ]

    errors = check_production_compose.validate_config(config)

    assert any(
        "Internal service alertmanager publishes host ports" in error for error in errors
    )


def test_application_runtimes_require_least_privilege_contract():
    config = _safe_config()
    config["services"]["backend"].update(
        {
            "user": "0:0",
            "read_only": False,
            "cap_drop": [],
            "security_opt": [],
            "tmpfs": [],
        }
    )

    errors = check_production_compose.validate_config(config)

    assert "backend must run as 10001:10001" in errors
    assert "backend must use a read-only root filesystem" in errors
    assert "backend must drop all Linux capabilities" in errors
    assert "backend must enforce no-new-privileges" in errors
    assert "backend must mount writable /tmp tmpfs" in errors


def test_application_runtime_rejects_unexpected_writable_app_mount():
    config = _safe_config()
    config["services"]["ops_jobs"]["volumes"] = [
        {"type": "volume", "source": "unexpected_cache", "target": "/app/cache"}
    ]

    errors = check_production_compose.validate_config(config)

    assert "ops_jobs has unexpected writable application mount: /app/cache" in errors


def test_required_writable_application_volume_cannot_disappear():
    config = _safe_config()
    config["services"]["backend"]["volumes"] = [
        mount
        for mount in config["services"]["backend"]["volumes"]
        if mount.get("target") != "/app/exports"
    ]

    errors = check_production_compose.validate_config(config)

    assert "backend must mount writable /app/exports" in errors


def test_loader_resolves_both_files_and_all_production_profiles(monkeypatch, tmp_path):
    (tmp_path / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    (tmp_path / "docker-compose.production.yml").write_text(
        "services: {}\n", encoding="utf-8"
    )
    observed = {}

    def fake_run(command, **kwargs):
        observed["command"] = command
        observed["cwd"] = kwargs.get("cwd")
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps(_safe_config()),
            stderr="",
        )

    monkeypatch.setattr(check_production_compose.subprocess, "run", fake_run)

    loaded = check_production_compose.load_compose_config(tmp_path)

    assert loaded == _safe_config()
    assert observed["cwd"] == tmp_path.resolve()
    assert observed["command"][:2] == ["docker", "compose"]
    assert str(tmp_path / "docker-compose.yml") in observed["command"]
    assert str(tmp_path / "docker-compose.production.yml") in observed["command"]
    for profile in check_production_compose.PRODUCTION_PROFILES:
        index = observed["command"].index(profile)
        assert observed["command"][index - 1] == "--profile"
