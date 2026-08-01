import pytest
from fastapi import HTTPException

from backend.services.checkout_validation import normalize_checkout_input


def normalized(**overrides):
    values = {
        "name": "Пётр Фёдин",
        "phone": "+46 70 123 45 67",
        "delivery_type": "pickup",
        "address": "should be discarded",
        "comment": "Позвонить перед выдачей",
    }
    values.update(overrides)
    return normalize_checkout_input(**values)


def test_pickup_input_is_normalized_and_address_is_removed():
    result = normalized(
        name="  Пётр   Фёдин  ",
        phone=" +46   70 123 45 67 ",
        delivery_type=" PICKUP ",
        comment="  Позвонить перед выдачей  ",
    )

    assert result.name == "Пётр Фёдин"
    assert result.phone == "+46 70 123 45 67"
    assert result.delivery_type == "pickup"
    assert result.address == ""
    assert result.comment == "Позвонить перед выдачей"


def test_courier_address_is_required_and_normalized():
    result = normalized(
        delivery_type="courier",
        address="  Stockholm   Birger Jarlsgatan 10  ",
    )

    assert result.address == "Stockholm Birger Jarlsgatan 10"

    with pytest.raises(HTTPException) as error:
        normalized(delivery_type="courier", address="Short")
    assert error.value.status_code == 400
    assert error.value.detail == "Courier address is too short"


@pytest.mark.parametrize(
    "phone",
    [
        "call-me-1234567",
        "+46 12",
        "+1234567890123456",
        "",
    ],
)
def test_invalid_phone_numbers_are_rejected(phone):
    with pytest.raises(HTTPException) as error:
        normalized(phone=phone)

    assert error.value.status_code == 400
    assert error.value.detail == "Phone number is invalid"


def test_unknown_delivery_type_is_rejected():
    with pytest.raises(HTTPException) as error:
        normalized(delivery_type="drone")

    assert error.value.status_code == 400
    assert error.value.detail == "Unsupported delivery type"


def test_comment_is_rejected_instead_of_silently_truncated():
    with pytest.raises(HTTPException) as error:
        normalized(comment="x" * 1001)

    assert error.value.status_code == 400
    assert error.value.detail == "Comment is too long"


def test_name_and_address_limits_match_storefront_contract():
    with pytest.raises(HTTPException):
        normalized(name="x" * 121)

    with pytest.raises(HTTPException):
        normalized(delivery_type="courier", address="x" * 501)
