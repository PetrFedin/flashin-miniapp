import math

import pytest
from fastapi import HTTPException

from backend.api.delivery_providers import _provider_config, _provider_name


def test_provider_name_is_trimmed_and_bounded():
    assert _provider_name("  CDEK  ") == "CDEK"
    for invalid in ("", "x", "x" * 256):
        with pytest.raises(HTTPException) as caught:
            _provider_name(invalid)
        assert caught.value.status_code == 400


def test_provider_config_is_canonical_and_rejects_nan():
    assert _provider_config({"timeout": 10, "mode": "test"}) == '{"mode":"test","timeout":10}'

    with pytest.raises(HTTPException) as caught:
        _provider_config({"bad": math.nan})
    assert caught.value.status_code == 400


def test_provider_config_rejects_non_serializable_and_oversized_values():
    with pytest.raises(HTTPException) as non_serializable:
        _provider_config({"bad": object()})
    assert non_serializable.value.status_code == 400

    with pytest.raises(HTTPException) as oversized:
        _provider_config({"payload": "x" * (64 * 1024)})
    assert oversized.value.status_code == 413
