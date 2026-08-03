from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from pilot_runtime import _normalize_ids  # noqa: E402


def test_allowlist_is_unique_numeric_and_bounded():
    assert _normalize_ids(["123", "456"]) == ["123", "456"]
    with pytest.raises(ValueError, match="duplicates"):
        _normalize_ids(["123", "123"])
    with pytest.raises(ValueError, match="positive numeric"):
        _normalize_ids(["@user"])
    with pytest.raises(ValueError, match="at most 50"):
        _normalize_ids([str(index + 1) for index in range(51)])
