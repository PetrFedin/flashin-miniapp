from datetime import UTC, datetime


def utc_timestamp(value: datetime | None = None) -> str:
    """Return an explicit RFC 3339 UTC timestamp for generated artifacts."""
    current = value or datetime.now(UTC)
    if current.tzinfo is None:
        raise ValueError("Timestamp must be timezone-aware")
    return current.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
