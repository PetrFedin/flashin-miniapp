from __future__ import annotations

import socket

import httpx

SAFE_RETRY_PRE_DISPATCH = "safe_retry_pre_dispatch"
AMBIGUOUS_TRANSPORT = "ambiguous_transport"
AMBIGUOUS_CANCELLED = "ambiguous_cancelled"
AMBIGUOUS_HTTP = "ambiguous_http"
PERMANENT_CONTRACT = "permanent_contract"
PERMANENT_HTTP = "permanent_http"

WEBHOOK_DELIVERY_CLASSIFICATIONS = (
    SAFE_RETRY_PRE_DISPATCH,
    AMBIGUOUS_TRANSPORT,
    AMBIGUOUS_CANCELLED,
    AMBIGUOUS_HTTP,
    PERMANENT_CONTRACT,
    PERMANENT_HTTP,
)

_REVIEW_REQUIRED = {
    AMBIGUOUS_TRANSPORT,
    AMBIGUOUS_CANCELLED,
    AMBIGUOUS_HTTP,
    PERMANENT_CONTRACT,
    PERMANENT_HTTP,
}
_AMBIGUOUS = {
    AMBIGUOUS_TRANSPORT,
    AMBIGUOUS_CANCELLED,
    AMBIGUOUS_HTTP,
}
_ERROR_PREFIX = "classification="


def validate_delivery_classification(value: str) -> str:
    normalized = str(value or "").strip()
    if normalized not in WEBHOOK_DELIVERY_CLASSIFICATIONS:
        raise ValueError("Unsupported webhook delivery classification")
    return normalized


def review_required_for(classification: str) -> bool:
    return validate_delivery_classification(classification) in _REVIEW_REQUIRED


def ambiguous_classification(classification: str) -> bool:
    return validate_delivery_classification(classification) in _AMBIGUOUS


def encode_delivery_error(classification: str, error_type: str) -> str:
    normalized = validate_delivery_classification(classification)
    safe_error_type = "".join(
        ch for ch in str(error_type or "WebhookDeliveryError") if ch.isalnum() or ch in "._-"
    )[:120]
    return f"{_ERROR_PREFIX}{normalized};error={safe_error_type or 'WebhookDeliveryError'}"


def delivery_classification_from_error(error: str) -> str:
    raw = str(error or "")
    if not raw.startswith(_ERROR_PREFIX):
        return ""
    classification = raw[len(_ERROR_PREFIX):].split(";", 1)[0].strip()
    return classification if classification in WEBHOOK_DELIVERY_CLASSIFICATIONS else ""


def public_delivery_error(status: str, error: str) -> str:
    if not error:
        return ""
    if str(status or "").strip().lower() == "review_required":
        return "Webhook delivery outcome requires review"
    return "Webhook delivery failed"


def classify_pre_dispatch_exception(exc: Exception) -> str:
    """Classify failures before HTTP dispatch can have reached the receiver."""

    if isinstance(exc, ValueError):
        if isinstance(exc.__cause__, socket.gaierror):
            return SAFE_RETRY_PRE_DISPATCH
        return PERMANENT_CONTRACT
    if isinstance(exc, RuntimeError):
        return PERMANENT_CONTRACT
    return SAFE_RETRY_PRE_DISPATCH


def classify_transport_exception(exc: Exception) -> str:
    """Classify httpx transport failures conservatively around dispatch ambiguity."""

    if isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout)):
        return SAFE_RETRY_PRE_DISPATCH
    if isinstance(exc, httpx.TransportError):
        return AMBIGUOUS_TRANSPORT
    return AMBIGUOUS_TRANSPORT


def classify_http_status(status_code: int) -> str:
    code = int(status_code)
    if 400 <= code < 500 and code not in {408, 425, 429}:
        return PERMANENT_HTTP
    return AMBIGUOUS_HTTP
