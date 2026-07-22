import re

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from backend.middleware import metrics
from backend.middleware.metrics import MetricsMiddleware
from backend.middleware.request_context import RequestContextMiddleware


class _MetricRecorder:
    def __init__(self):
        self.labels_calls = []
        self.observations = []
        self.increments = 0

    def labels(self, *values):
        self.labels_calls.append(values)
        return self

    def inc(self):
        self.increments += 1

    def observe(self, value):
        self.observations.append(value)


def _request_context_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(RequestContextMiddleware)

    @app.get("/items/{item_id}")
    def get_item(item_id: int, request: Request):
        return {"item_id": item_id, "request_id": request.state.request_id}

    return app


def _metrics_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(MetricsMiddleware)

    @app.get("/items/{item_id}")
    def get_item(item_id: int):
        return {"item_id": item_id}

    return app


def test_request_context_preserves_safe_upstream_request_id():
    client = TestClient(_request_context_app())

    response = client.get("/items/42", headers={"X-Request-ID": "edge-req_42"})

    assert response.status_code == 200
    assert response.headers["X-Request-ID"] == "edge-req_42"
    assert response.json()["request_id"] == "edge-req_42"
    assert re.fullmatch(r"app;dur=\d+\.\d{2}", response.headers["Server-Timing"])


def test_request_context_replaces_malformed_request_id():
    client = TestClient(_request_context_app())

    response = client.get("/items/42", headers={"X-Request-ID": "contains spaces"})

    generated_request_id = response.headers["X-Request-ID"]
    assert response.status_code == 200
    assert generated_request_id != "contains spaces"
    assert re.fullmatch(r"[0-9a-f]{32}", generated_request_id)
    assert response.json()["request_id"] == generated_request_id


def test_metrics_use_route_template_instead_of_raw_resource_id(monkeypatch):
    request_count = _MetricRecorder()
    request_latency = _MetricRecorder()
    monkeypatch.setattr(metrics, "REQUEST_COUNT", request_count)
    monkeypatch.setattr(metrics, "REQUEST_LATENCY", request_latency)
    client = TestClient(_metrics_app())

    response = client.get("/items/987654")

    assert response.status_code == 200
    assert request_count.labels_calls == [("GET", "/items/{item_id}", "200")]
    assert request_latency.labels_calls == [("GET", "/items/{item_id}")]
    assert request_count.increments == 1
    assert len(request_latency.observations) == 1


def test_metrics_collapse_unknown_paths_into_single_label(monkeypatch):
    request_count = _MetricRecorder()
    request_latency = _MetricRecorder()
    monkeypatch.setattr(metrics, "REQUEST_COUNT", request_count)
    monkeypatch.setattr(metrics, "REQUEST_LATENCY", request_latency)
    client = TestClient(_metrics_app())

    response = client.get("/unknown/customer-controlled/path")

    assert response.status_code == 404
    assert request_count.labels_calls == [("GET", "__unmatched__", "404")]
    assert request_latency.labels_calls == [("GET", "__unmatched__")]
