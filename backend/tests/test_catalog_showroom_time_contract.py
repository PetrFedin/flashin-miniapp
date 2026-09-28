from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException

from backend.api.catalog_showroom import _utc_iso, _utc_naive
from backend.main import app


_HTTP_METHODS = {"get", "post", "put", "patch", "delete", "options", "head", "trace"}


def test_showroom_aware_time_is_normalized_to_utc_without_clock_drift():
    berlin_summer = timezone(timedelta(hours=2))
    value = datetime(2026, 8, 20, 12, 30, tzinfo=berlin_summer)

    assert _utc_naive(value) == datetime(2026, 8, 20, 10, 30)
    assert _utc_iso(datetime(2026, 8, 20, 10, 30)) == "2026-08-20T10:30:00Z"


def test_showroom_rejects_naive_client_time():
    with pytest.raises(HTTPException) as exc_info:
        _utc_naive(datetime(2026, 8, 20, 12, 30))

    assert exc_info.value.status_code == 400
    assert "timezone" in str(exc_info.value.detail).lower()


def test_runtime_has_exactly_one_route_for_each_showroom_operation():
    required = {
        ("/api/catalog/showroom/appointments", "POST"),
        ("/api/catalog/showroom/appointments/me", "GET"),
        ("/api/catalog/admin/showroom/appointments", "GET"),
        ("/api/catalog/admin/showroom/appointments/{appointment_id}", "PATCH"),
    }
    counts = {item: 0 for item in required}
    for path, path_item in app.openapi()["paths"].items():
        for method in path_item:
            if method.lower() not in _HTTP_METHODS:
                continue
            item = (str(path), str(method).upper())
            if item in counts:
                counts[item] += 1

    assert counts == {item: 1 for item in required}
