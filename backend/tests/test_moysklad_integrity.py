from backend.services.moysklad import (
    _price_from_moysklad,
    _slugify,
    _stock_from_moysklad,
)


def test_missing_stock_is_not_interpreted_as_zero():
    assert _stock_from_moysklad({"id": "item-1"}) is None


def test_invalid_stock_is_not_applied():
    assert _stock_from_moysklad({"stock": "not-a-number"}) is None
    assert _stock_from_moysklad({"effectiveStock": float("nan")}) is None


def test_stock_priority_and_negative_clamp():
    assert _stock_from_moysklad({"quantity": 9, "stock": 7, "effectiveStock": 5}) == 5
    assert _stock_from_moysklad({"stock": -3}) == 0


def test_price_uses_first_valid_positive_sale_price():
    row = {
        "salePrices": [
            {"value": None},
            {"value": -100},
            {"value": 129900},
        ]
    }
    assert _price_from_moysklad(row) == 1299.0


def test_slugify_is_stable_and_bounded():
    assert _slugify("FLASHIN Coat / Black") == "flashin-coat-black"
    assert _slugify("---") == "product"
    assert len(_slugify("A" * 400)) <= 230
