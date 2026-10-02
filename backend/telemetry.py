from __future__ import annotations

from typing import TYPE_CHECKING

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased

from .services.release_identity import runtime_git_sha

if TYPE_CHECKING:
    from fastapi import FastAPI
    from sqlalchemy import Engine

    from .config import Settings


def normalize_otlp_trace_endpoint(value: str) -> str:
    endpoint = str(value or "").strip().rstrip("/")
    if not endpoint:
        return ""
    if endpoint.endswith("/v1/traces"):
        return endpoint
    return f"{endpoint}/v1/traces"


def telemetry_resource_attributes(settings: "Settings") -> dict[str, str]:
    release = runtime_git_sha() or "unknown"
    return {
        "service.name": settings.otel_service_name.strip() or "flashin-api",
        "service.namespace": "flashin",
        "service.version": release,
        "deployment.environment.name": settings.app_env.strip().lower() or "unknown",
    }


def configure_tracing(app: "FastAPI", engine: "Engine", settings: "Settings") -> bool:
    """Enable release-bound OTLP tracing without replacing Sentry or Prometheus."""

    if not settings.otel_tracing_enabled:
        return False
    if getattr(app.state, "otel_tracing_configured", False):
        return True

    endpoint = normalize_otlp_trace_endpoint(settings.otel_exporter_otlp_endpoint)
    if not endpoint:
        raise RuntimeError("OTEL_EXPORTER_OTLP_ENDPOINT is required when tracing is enabled")

    resource = Resource.create(telemetry_resource_attributes(settings))
    provider = TracerProvider(
        resource=resource,
        sampler=ParentBased(
            root=TraceIdRatioBased(float(settings.otel_trace_sample_ratio))
        ),
    )
    provider.add_span_processor(
        BatchSpanProcessor(
            OTLPSpanExporter(endpoint=endpoint),
        )
    )
    trace.set_tracer_provider(provider)

    FastAPIInstrumentor.instrument_app(
        app,
        tracer_provider=provider,
        excluded_urls=settings.otel_excluded_urls,
        exclude_spans=["receive", "send"],
    )
    SQLAlchemyInstrumentor().instrument(
        engine=engine,
        tracer_provider=provider,
        enable_commenter=False,
    )

    app.state.otel_tracing_configured = True
    return True


__all__ = [
    "configure_tracing",
    "normalize_otlp_trace_endpoint",
    "telemetry_resource_attributes",
]
