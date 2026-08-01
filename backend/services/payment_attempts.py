from dataclasses import dataclass


_ACTIVE_PROVIDER_STATUSES = frozenset({"pending", "waiting_for_capture"})
_SETTLED_PROVIDER_STATUSES = frozenset({"succeeded"})
_LIVE_PROVIDER_STATUSES = _ACTIVE_PROVIDER_STATUSES | _SETTLED_PROVIDER_STATUSES
_REPLACEABLE_PROVIDER_STATUSES = frozenset({"canceled"})


@dataclass(frozen=True)
class PaymentAttemptResolution:
    outcome: str
    status: str
    confirmation_url: str = ""


def _confirmation_url(provider_payment: dict) -> str:
    confirmation = provider_payment.get("confirmation")
    if not isinstance(confirmation, dict):
        return ""
    value = str(confirmation.get("confirmation_url") or "").strip()
    return value[:2048]


def resolve_provider_payment_attempt(
    provider_payment: dict,
    *,
    stored_confirmation_url: str = "",
) -> PaymentAttemptResolution:
    status = str(provider_payment.get("status") or "").strip().lower()
    provider_url = _confirmation_url(provider_payment)
    stored_url = str(stored_confirmation_url or "").strip()[:2048]

    if status in _ACTIVE_PROVIDER_STATUSES:
        confirmation_url = provider_url or stored_url
        if confirmation_url:
            return PaymentAttemptResolution("reuse", status, confirmation_url)
        return PaymentAttemptResolution("unavailable", status)

    if status in _SETTLED_PROVIDER_STATUSES:
        return PaymentAttemptResolution("settled", status)

    if status in _REPLACEABLE_PROVIDER_STATUSES:
        return PaymentAttemptResolution("replace", status)

    return PaymentAttemptResolution("review", status or "unknown")


def can_fallback_to_stored_attempt(status: str, confirmation_url: str) -> bool:
    normalized_status = str(status or "").strip().lower()
    normalized_url = str(confirmation_url or "").strip()
    return normalized_status in _ACTIVE_PROVIDER_STATUSES and bool(normalized_url)


def is_stale_cancellation(
    canceled_provider_payment_id: str,
    latest_provider_payment_id: str,
    latest_status: str,
) -> bool:
    canceled_id = str(canceled_provider_payment_id or "").strip()
    latest_id = str(latest_provider_payment_id or "").strip()
    normalized_status = str(latest_status or "").strip().lower()
    return bool(
        canceled_id
        and latest_id
        and canceled_id != latest_id
        and normalized_status in _LIVE_PROVIDER_STATUSES
    )
