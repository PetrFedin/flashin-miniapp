from unittest.mock import Mock

import pytest
from fastapi import HTTPException
from sqlalchemy.exc import OperationalError

from backend.api.health import health, ready


def test_health_is_independent_from_external_services():
    assert health() == {"status": "ok"}


def test_ready_returns_database_status_when_query_succeeds():
    db = Mock()

    response = ready(db)

    db.execute.assert_called_once()
    assert response == {"status": "ready", "database": "ok"}


def test_ready_returns_503_when_database_is_unavailable():
    db = Mock()
    db.execute.side_effect = OperationalError(
        statement="SELECT 1",
        params=None,
        orig=RuntimeError("database unavailable"),
    )

    with pytest.raises(HTTPException) as exc_info:
        ready(db)

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail == {
        "status": "not_ready",
        "database": "unavailable",
    }
