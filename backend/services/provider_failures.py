from __future__ import annotations

from fastapi import HTTPException

_TRANSIENT_PROVIDER_STATUSES = frozenset({429, 500, 502, 503, 504})
_RETRYABLE_PROVIDER_ERRORS = frozenset({"network_error"})


def is_retryable_yookassa_error(exc: HTTPException) -> bool:
    """Return True only when replaying the same idempotent request is safe and useful."""

    if not isinstance(exc, HTTPException):
        return False
    detail = exc.detail
    if not isinstance(detail, dict):
        return False

    error = str(detail.get("error") or "").strip().lower()
    if error in _RETRYABLE_PROVIDER_ERRORS:
        return True

    raw_status = detail.get("status_code")
    if isinstance(raw_status, bool):
        return False
    try:
        provider_status = int(raw_status)
    except (TypeError, ValueError):
        return False
    return provider_status in _TRANSIENT_PROVIDER_STATUSES
