from datetime import UTC, datetime, timedelta, timezone

import pytest

from scripts.script_time import utc_timestamp


def test_utc_timestamp_is_rfc3339_with_z_suffix():
    value = utc_timestamp(datetime(2026, 8, 1, 21, 15, 42, tzinfo=UTC))

    assert value == "2026-08-01T21:15:42Z"


def test_utc_timestamp_normalizes_non_utc_offset():
    stockholm_summer = timezone(timedelta(hours=2))

    value = utc_timestamp(datetime(2026, 8, 1, 23, 15, 42, tzinfo=stockholm_summer))

    assert value == "2026-08-01T21:15:42Z"


def test_utc_timestamp_rejects_naive_values():
    with pytest.raises(ValueError, match="timezone-aware"):
        utc_timestamp(datetime(2026, 8, 1, 21, 15, 42))
