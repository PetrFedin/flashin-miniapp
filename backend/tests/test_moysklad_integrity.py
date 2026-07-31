from backend.services.moysklad import (
    _attribute_value,
    _price_from_moysklad,
    _row_type,
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


def test_price_uses_first_valid_positive_sale_price_without_configuration():
    row = {
        "salePrices": [
            {"value": None},
            {"value": -100},
            {"value": 129900},
        ]
    }
    assert _price_from_moysklad(row) == 1299.0


def test_price_selects_configured_type_by_name_id_or_href():
    row = {
        "salePrices": [
            {
                "value": 99000,
                "priceType": {"name": "Оптовая цена", "id": "wholesale"},
            },
            {
                "value": 149900,
                "priceType": {
                    "name": "Розничная цена",
                    "meta": {
                        "href": "https://api.moysklad.ru/api/remap/1.2/context/companysettings/pricetype/retail-id"
                    },
                },
            },
        ]
    }

    assert _price_from_moysklad(row, "Розничная цена") == 1499.0
    assert _price_from_moysklad(row, "wholesale") == 990.0
    assert _price_from_moysklad(row, "retail-id") == 1499.0


def test_configured_price_type_never_silently_falls_back():
    row = {
        "salePrices": [
            {"value": 99000, "priceType": {"name": "Оптовая цена"}},
        ]
    }
    assert _price_from_moysklad(row, "Розничная цена") == 0.0


def test_size_and_color_are_read_from_configured_attributes():
    row = {
        "attributes": [
            {"name": "Размер", "value": {"name": "M"}},
            {"name": "Цвет", "value": "Black"},
        ],
        "uom": {"name": "шт"},
    }

    assert _attribute_value(row, "Размер,Size", direct_keys=("size",)) == "M"
    assert _attribute_value(row, "Цвет,Color", direct_keys=("color",)) == "Black"


def test_unit_of_measure_is_not_used_as_clothing_size():
    row = {"uom": {"name": "шт"}, "attributes": []}
    assert _attribute_value(row, "Размер,Size", direct_keys=("size",)) == ""


def test_direct_size_and_color_take_priority_over_attributes():
    row = {
        "size": "L",
        "color": "White",
        "attributes": [
            {"name": "Размер", "value": "M"},
            {"name": "Цвет", "value": "Black"},
        ],
    }
    assert _attribute_value(row, "Размер", direct_keys=("size",)) == "L"
    assert _attribute_value(row, "Цвет", direct_keys=("color",)) == "White"


def test_variant_type_is_detected_from_meta():
    assert _row_type({"meta": {"type": "variant"}}) == "variant"
    assert _row_type({"type": "product"}) == "product"


def test_slugify_is_stable_and_bounded():
    assert _slugify("FLASHIN Coat / Black") == "flashin-coat-black"
    assert _slugify("---") == "product"
    assert len(_slugify("A" * 400)) <= 230
