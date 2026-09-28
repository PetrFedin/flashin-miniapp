import pytest

from backend.services import delivery


class NoCommercialStateSession:
    """Fail if the retired calculator tries to discover commercial state."""

    def query(self, _model):
        raise AssertionError("legacy delivery calculator must not query tariff state")


def test_legacy_delivery_calculator_is_fail_closed_for_courier():
    with pytest.raises(RuntimeError, match="quote-authoritative"):
        delivery.calculate_delivery_price(NoCommercialStateSession(), "courier")


def test_legacy_delivery_calculator_is_fail_closed_for_pickup():
    with pytest.raises(RuntimeError, match="quote-authoritative"):
        delivery.calculate_delivery_price(NoCommercialStateSession(), "pickup")


def test_legacy_delivery_calculator_never_reintroduces_unknown_type_fallback():
    with pytest.raises(RuntimeError, match="quote-authoritative"):
        delivery.calculate_delivery_price(NoCommercialStateSession(), "drone")
