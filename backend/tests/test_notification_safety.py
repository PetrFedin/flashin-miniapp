from backend.services.notifications import (
    _normalize_message,
    _normalize_telegram_id,
)


def test_telegram_id_must_be_numeric_and_nonzero():
    assert _normalize_telegram_id("123456") == "123456"
    assert _normalize_telegram_id("-100123456") == "-100123456"
    assert _normalize_telegram_id("0") is None
    assert _normalize_telegram_id("not-an-id") is None


def test_deleted_customer_is_not_queued():
    assert _normalize_telegram_id("deleted:12:abcdef") is None


def test_notification_message_is_trimmed_to_telegram_limit():
    normalized = _normalize_message("x" * 5000)

    assert normalized is not None
    assert len(normalized) == 4096
    assert normalized.endswith("…")


def test_empty_notification_is_not_queued():
    assert _normalize_message("   ") is None
