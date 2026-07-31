import json
from types import SimpleNamespace

from scripts import check_production_compose


def _safe_config():
    services = {
        name: {}
        for name in check_production_compose.REQUIRED_INTERNAL_SERVICES
    }
    services[check_production_compose.PUBLIC_SERVICE] = {
        "ports": [
            {"published": "80", "target": 80, "protocol": "tcp"},
            {"published": "443", "target": 443, "protocol": "tcp"},
        ]
    }
    return {"services": services}


def test_safe_production_graph_passes():
    assert check_production_compose.validate_config(_safe_config()) == []


def test_internal_host_port_is_rejected():
    config = _safe_config()
    config["services"]["backend"]["ports"] = [
        {"published": "8000", "target": 8000, "protocol": "tcp"}
    ]

    errors = check_production_compose.validate_config(config)

    assert any("Internal service backend publishes host ports" in error for error in errors)


def test_loader_resolves_both_files_and_all_production_profiles(monkeypatch, tmp_path):
    (tmp_path / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    (tmp_path / "docker-compose.production.yml").write_text("services: {}\n", encoding="utf-8")
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
