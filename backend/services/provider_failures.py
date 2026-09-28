from __future__ import annotations

import json

from fastapi import HTTPException

_TRANSIENT_PROVIDER_STATUSES = frozenset({408, 409, 425, 429, 500, 502, 503, 504})
_RETRYABLE_PROVIDER_ERRORS = frozenset({"network_error"})
_MAX_REASON_LENGTH = 2000


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


def yookassa_error_reason(exc: BaseException) -> str:
    """Create a bounded deterministic internal reason without serializing secrets."""

    if isinstance(exc, HTTPException):
        detail = exc.detail
        if isinstance(detail, dict):
            safe_detail = {
                key: detail.get(key)
                for key in ("provider", "error", "status_code")
                if detail.get(key) not in (None, "")
            }
            if safe_detail:
                return json.dumps(
                    safe_detail,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )[:_MAX_REASON_LENGTH]
        text = str(detail or "provider_request_failed").strip()
        return (text or "provider_request_failed")[:_MAX_REASON_LENGTH]

    name = exc.__class__.__name__.strip() or "UnexpectedProviderFailure"
    return f"unexpected_provider_failure:{name}"[:_MAX_REASON_LENGTH]
