from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _source(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_provider_tracing_is_explicit_and_does_not_enable_automatic_http_url_capture():
    telemetry = _source("backend/telemetry.py")
    requirements = _source("backend/requirements.txt")

    assert "HTTPXClientInstrumentor" not in telemetry
    assert "opentelemetry-instrumentation-httpx" not in requirements


def test_provider_spans_use_bounded_metadata_only():
    payments = _source("backend/services/payments.py")
    moysklad = _source("backend/services/moysklad.py")
    outbound = _source("backend/services/moysklad_outbound.py")

    for source in (payments, moysklad, outbound):
        assert '"flashin.provider.http"' in source
        assert '"flashin.provider"' in source
        assert '"flashin.provider.method"' in source
        assert '"flashin.provider.status_class"' in source
        assert "record_exception=False" in source
        assert "set_status_on_exception=False" in source
        assert 'span.set_attribute("url' not in source
        assert 'span.set_attribute("http.url' not in source
        assert 'span.set_attribute("payload' not in source
        assert 'span.set_attribute("headers' not in source
        assert 'span.set_attribute("authorization' not in source.lower()


def test_rate_limit_spans_never_attach_redis_keys_or_urls():
    source = _source("backend/services/distributed_rate_limit.py")

    assert '"flashin.rate_limit.key_count"' in source
    assert '"flashin.rate_limit.allowed"' in source
    assert "record_exception=False" in source
    assert "set_status_on_exception=False" in source
    assert 'span.set_attribute("flashin.rate_limit.keys"' not in source
    assert 'span.set_attribute("redis_url"' not in source
    assert 'span.set_attribute("rate_limit_redis_url"' not in source


def test_provider_job_span_never_attaches_command_payload_or_lease_token():
    source = _source("backend/jobs/provider_command_jobs.py")

    assert '"flashin.provider_command.id"' in source
    assert '"flashin.provider_command.type"' in source
    assert '"flashin.provider_command.status"' in source
    assert "record_exception=False" in source
    assert "set_status_on_exception=False" in source
    assert 'span.set_attribute("payload' not in source
    assert 'span.set_attribute("lease_token' not in source
