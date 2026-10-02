from types import SimpleNamespace

from backend.telemetry import (
    configure_tracing,
    normalize_otlp_trace_endpoint,
    telemetry_resource_attributes,
)


def _settings(**overrides):
    values = {
        "otel_tracing_enabled": False,
        "otel_service_name": "flashin-api",
        "otel_exporter_otlp_endpoint": "",
        "otel_trace_sample_ratio": 0.1,
        "otel_excluded_urls": "/health,/ready,/release,/metrics",
        "app_env": "production",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_otlp_endpoint_normalization_appends_trace_path():
    assert normalize_otlp_trace_endpoint("https://collector.example.test") == (
        "https://collector.example.test/v1/traces"
    )
    assert normalize_otlp_trace_endpoint(
        "https://collector.example.test/v1/traces"
    ) == "https://collector.example.test/v1/traces"


def test_otlp_endpoint_normalization_preserves_disabled_state():
    assert normalize_otlp_trace_endpoint("") == ""


def test_resource_attributes_bind_service_environment_and_release(monkeypatch):
    monkeypatch.setenv("RENDER_GIT_COMMIT", "a" * 40)
    attributes = telemetry_resource_attributes(_settings())

    assert attributes["service.name"] == "flashin-api"
    assert attributes["service.namespace"] == "flashin"
    assert attributes["service.version"] == "a" * 40
    assert attributes["deployment.environment.name"] == "production"


def test_configure_tracing_is_noop_when_disabled():
    app = SimpleNamespace(state=SimpleNamespace())
    engine = object()

    assert configure_tracing(app, engine, _settings()) is False
    assert not hasattr(app.state, "otel_tracing_configured")
