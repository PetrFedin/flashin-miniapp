from __future__ import annotations

import time
from typing import Any, TYPE_CHECKING

from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

from ..services.pilot_observability import build_pilot_operations_status

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from ..config import Settings

REQUEST_COUNT = Counter(
    "flashin_http_requests_total",
    "HTTP requests",
    ["method", "path", "status"],
)
REQUEST_LATENCY = Histogram(
    "flashin_http_request_latency_seconds",
    "HTTP request latency",
    ["method", "path"],
)

PILOT_METRICS_COLLECTION_SUCCESS = Gauge(
    "flashin_pilot_metrics_collection_success",
    "Whether the latest pilot metric collection completed successfully",
)
PILOT_RUNTIME_ENFORCED = Gauge(
    "flashin_pilot_runtime_enforced",
    "Whether the controlled pilot runtime guard is enforced",
)
PILOT_CHECKOUT_READY = Gauge(
    "flashin_pilot_checkout_ready",
    "Whether the next controlled pilot checkout is allowed by the verified runtime status",
)
PILOT_RUNTIME_STATUS = Gauge(
    "flashin_pilot_runtime_status",
    "Current controlled pilot runtime status as a fixed one-hot series",
    ["status"],
)
PILOT_ORDERS_ACCEPTED = Gauge(
    "flashin_pilot_orders_accepted",
    "Number of orders accepted by the current controlled pilot run",
)
PILOT_ORDERS_REMAINING = Gauge(
    "flashin_pilot_orders_remaining",
    "Number of order slots remaining in the controlled pilot run",
)
PILOT_ORDER_SLOTS = Gauge(
    "flashin_pilot_order_slots",
    "Number of persisted order slots for the current controlled pilot run",
)
PILOT_ALLOWLIST_SIZE = Gauge(
    "flashin_pilot_allowlist_size",
    "Count of approved pilot users without exposing their identifiers",
)
PILOT_DATABASE_INTEGRITY_HEALTHY = Gauge(
    "flashin_pilot_database_integrity_healthy",
    "Whether pilot runtime database counters and slot bindings are internally consistent",
)
PILOT_ARTIFACT_INTEGRITY_APPLICABLE = Gauge(
    "flashin_pilot_artifact_integrity_applicable",
    "Whether signed pilot admission and release artifact validation applies",
)
PILOT_ARTIFACT_INTEGRITY_HEALTHY = Gauge(
    "flashin_pilot_artifact_integrity_healthy",
    "Whether signed pilot admission, evidence and release artifacts are valid",
)
PILOT_MONEY_ATTENTION = Gauge(
    "flashin_pilot_money_attention",
    "Count of unresolved pilot money-integrity signals by fixed category",
    ["kind"],
)

_RUNTIME_STATUSES = ("not_armed", "active", "stopped", "completed", "unknown")
_MONEY_KINDS = (
    "payment_review",
    "refund_attention",
    "reconciliation_mismatch",
)


def _metric_path(request: Any) -> str:
    route = request.scope.get("route") if hasattr(request, "scope") else None
    route_path = getattr(route, "path", None)
    if isinstance(route_path, str) and route_path.startswith("/"):
        return route_path
    return "__unmatched__"


def _reset_pilot_metrics(*, enforced: bool) -> None:
    PILOT_METRICS_COLLECTION_SUCCESS.set(0)
    PILOT_RUNTIME_ENFORCED.set(1 if enforced else 0)
    PILOT_CHECKOUT_READY.set(0)
    for status in _RUNTIME_STATUSES:
        PILOT_RUNTIME_STATUS.labels(status=status).set(0)
    PILOT_RUNTIME_STATUS.labels(status="unknown").set(1)
    PILOT_ORDERS_ACCEPTED.set(0)
    PILOT_ORDERS_REMAINING.set(0)
    PILOT_ORDER_SLOTS.set(0)
    PILOT_ALLOWLIST_SIZE.set(0)
    PILOT_DATABASE_INTEGRITY_HEALTHY.set(0)
    PILOT_ARTIFACT_INTEGRITY_APPLICABLE.set(0)
    PILOT_ARTIFACT_INTEGRITY_HEALTHY.set(0)
    for kind in _MONEY_KINDS:
        PILOT_MONEY_ATTENTION.labels(kind=kind).set(0)


def collect_pilot_metrics(db: "Session", settings: "Settings") -> bool:
    enforced = bool(settings.pilot_runtime_enforced)
    _reset_pilot_metrics(enforced=enforced)
    try:
        snapshot = build_pilot_operations_status(db, settings)
        runtime = snapshot["runtime"]
        database = snapshot["database_integrity"]
        artifacts = snapshot["artifact_integrity"]
        money = snapshot["money_attention"]
        status = str(runtime["status"])
        if status not in _RUNTIME_STATUSES[:-1]:
            raise ValueError("Unsupported pilot runtime status")

        for candidate in _RUNTIME_STATUSES:
            PILOT_RUNTIME_STATUS.labels(status=candidate).set(1 if candidate == status else 0)
        PILOT_CHECKOUT_READY.set(1 if snapshot["checkout_decision"] == "GO" else 0)
        PILOT_ORDERS_ACCEPTED.set(int(runtime["accepted_orders"]))
        PILOT_ORDERS_REMAINING.set(int(runtime["remaining_orders"]))
        PILOT_ORDER_SLOTS.set(int(runtime["slot_count"]))
        PILOT_ALLOWLIST_SIZE.set(int(runtime["allowlist_count"]))
        PILOT_DATABASE_INTEGRITY_HEALTHY.set(1 if database["healthy"] else 0)
        PILOT_ARTIFACT_INTEGRITY_APPLICABLE.set(1 if artifacts["applicable"] else 0)
        PILOT_ARTIFACT_INTEGRITY_HEALTHY.set(1 if artifacts["healthy"] is True else 0)
        PILOT_MONEY_ATTENTION.labels(kind="payment_review").set(
            int(money["payment_review_orders"])
        )
        PILOT_MONEY_ATTENTION.labels(kind="refund_attention").set(
            int(money["refund_attention_orders"])
        )
        PILOT_MONEY_ATTENTION.labels(kind="reconciliation_mismatch").set(
            int(money["reconciliation_mismatches"])
        )
        PILOT_METRICS_COLLECTION_SUCCESS.set(1)
        return True
    except (KeyError, TypeError, ValueError, RuntimeError):
        return False


class MetricsMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        start = time.monotonic()
        response = await call_next(request)
        path = _metric_path(request)
        REQUEST_COUNT.labels(request.method, path, str(response.status_code)).inc()
        REQUEST_LATENCY.labels(request.method, path).observe(time.monotonic() - start)
        return response


def metrics_response():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
