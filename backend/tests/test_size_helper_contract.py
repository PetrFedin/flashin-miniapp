import pytest

from backend.services.size_helper import suggest_size


def test_usual_size_and_fit_drive_explainable_result():
    result = suggest_size(
        height_cm=180,
        weight_kg=80,
        usual_size="M",
        fit_preference="oversize",
        available_sizes=["L", "XL"],
    )

    assert result["base_size"] == "L"
    assert result["suggested_size"] == "L"
    assert result["exact_available"] is True
    assert result["confidence"] == "medium"
    assert "usual_size:M" in result["basis"]
    assert "fit:oversize" in result["basis"]
    assert "замеры конкретного изделия" in result["note"]


def test_nearest_available_size_is_returned_without_false_exactness():
    result = suggest_size(
        height_cm=178,
        weight_kg=78,
        usual_size="M",
        fit_preference="regular",
        available_sizes=["S", "XL"],
    )

    assert result["base_size"] == "M"
    assert result["suggested_size"] == "S"
    assert result["exact_available"] is False
    assert "Ближайший" in result["note"] or "ближайший" in result["note"]


def test_product_with_no_available_sizes_keeps_base_for_restock_flow():
    result = suggest_size(
        height_cm=180,
        weight_kg=80,
        usual_size="48",
        fit_preference="regular",
        available_sizes=[],
    )

    assert result["base_size"] == "L"
    assert result["suggested_size"] == "L"
    assert result["exact_available"] is False


def test_measurement_only_result_is_marked_low_confidence():
    result = suggest_size(
        height_cm=182,
        weight_kg=84,
        usual_size=None,
        fit_preference="regular",
    )

    assert result["confidence"] == "low"
    assert result["suggested_size"] in {"M", "L", "XL"}


@pytest.mark.parametrize(
    "kwargs",
    [
        {"height_cm": 120, "weight_kg": 70, "usual_size": None},
        {"height_cm": 180, "weight_kg": 20, "usual_size": None},
        {"height_cm": 180, "weight_kg": None, "usual_size": None},
        {"height_cm": 180, "weight_kg": 80, "usual_size": "INVALID"},
    ],
)
def test_invalid_or_insufficient_inputs_are_rejected(kwargs):
    with pytest.raises(ValueError):
        suggest_size(fit_preference="regular", **kwargs)
