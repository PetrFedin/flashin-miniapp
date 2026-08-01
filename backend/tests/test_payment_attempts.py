from backend.services.payment_attempts import (
    can_fallback_to_stored_attempt,
    is_stale_cancellation,
    resolve_provider_payment_attempt,
)


def test_pending_payment_uses_fresh_provider_confirmation_url():
    resolution = resolve_provider_payment_attempt(
        {
            "status": "pending",
            "confirmation": {"confirmation_url": " https://pay.example/new "},
        },
        stored_confirmation_url="https://pay.example/old",
    )

    assert resolution.outcome == "reuse"
    assert resolution.status == "pending"
    assert resolution.confirmation_url == "https://pay.example/new"


def test_waiting_payment_can_use_stored_url_when_provider_omits_confirmation():
    resolution = resolve_provider_payment_attempt(
        {"status": "waiting_for_capture"},
        stored_confirmation_url="https://pay.example/stored",
    )

    assert resolution.outcome == "reuse"
    assert resolution.confirmation_url == "https://pay.example/stored"


def test_pending_payment_without_any_url_is_not_replaced_automatically():
    resolution = resolve_provider_payment_attempt({"status": "pending"})

    assert resolution.outcome == "unavailable"
    assert resolution.confirmation_url == ""


def test_succeeded_payment_is_settled_instead_of_recreated():
    resolution = resolve_provider_payment_attempt(
        {
            "status": "succeeded",
            "confirmation": {"confirmation_url": "https://pay.example/stale"},
        }
    )

    assert resolution.outcome == "settled"
    assert resolution.confirmation_url == ""


def test_canceled_payment_allows_a_new_attempt():
    resolution = resolve_provider_payment_attempt({"status": "canceled"})

    assert resolution.outcome == "replace"
    assert resolution.status == "canceled"


def test_unknown_provider_status_requires_review():
    resolution = resolve_provider_payment_attempt({"status": "mystery"})

    assert resolution.outcome == "review"
    assert resolution.status == "mystery"


def test_stored_attempt_is_only_fallback_for_active_payment_with_url():
    assert can_fallback_to_stored_attempt("pending", "https://pay.example") is True
    assert can_fallback_to_stored_attempt("waiting_for_capture", "https://pay.example") is True
    assert can_fallback_to_stored_attempt("succeeded", "https://pay.example") is False
    assert can_fallback_to_stored_attempt("pending", "") is False


def test_old_cancellation_is_stale_when_newer_live_attempt_exists():
    assert is_stale_cancellation("pay-1", "pay-2", "pending") is True
    assert is_stale_cancellation("pay-1", "pay-2", "waiting_for_capture") is True
    assert is_stale_cancellation("pay-1", "pay-2", "succeeded") is True


def test_latest_or_non_live_cancellation_is_not_ignored():
    assert is_stale_cancellation("pay-2", "pay-2", "pending") is False
    assert is_stale_cancellation("pay-1", "pay-2", "canceled") is False
    assert is_stale_cancellation("", "pay-2", "pending") is False
