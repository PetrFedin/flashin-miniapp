import pytest
from pydantic import ValidationError

from backend.config import Settings


def _base_env(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "dev-token")
    monkeypatch.setenv("JWT_SECRET", "dev-secret")


def test_tracing_requires_http_otlp_endpoint_when_enabled(monkeypatch):
    _base_env(monkeypatch)
    monkeypatch.setenv("OTEL_TRACING_ENABLED", "true")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "grpc://collector:4317")

    with pytest.raises(ValidationError):
        Settings()


def test_trace_sample_ratio_is_bounded(monkeypatch):
    _base_env(monkeypatch)
    monkeypatch.setenv("OTEL_TRACE_SAMPLE_RATIO", "1.1")

    with pytest.raises(ValidationError):
        Settings()


def test_tracing_defaults_to_disabled(monkeypatch):
    _base_env(monkeypatch)

    settings = Settings()

    assert settings.otel_tracing_enabled is False
    assert settings.otel_service_name == "flashin-api"
    assert settings.otel_trace_sample_ratio == 0.1
