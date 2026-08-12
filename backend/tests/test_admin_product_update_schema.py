import math

import pytest
from pydantic import ValidationError

from backend.schemas import AdminProductUpdate


def test_admin_product_update_trims_and_normalizes_master_data():
    payload = AdminProductUpdate(
        title="  Pilot Jacket  ",
        brand=" FLASHIN ",
        category=" Outerwear ",
        description="  Limited pilot item  ",
        price=12500.129,
        active=True,
    )

    assert payload.title == "Pilot Jacket"
    assert payload.brand == "FLASHIN"
    assert payload.category == "Outerwear"
    assert payload.description == "Limited pilot item"
    assert payload.price == 12500.13
    assert payload.active is True


@pytest.mark.parametrize("field", ["title", "brand", "category"])
def test_admin_product_update_rejects_blank_required_master_data(field):
    with pytest.raises(ValidationError):
        AdminProductUpdate(**{field: "   "})


@pytest.mark.parametrize("field", ["title", "brand", "category", "description", "price", "active"])
def test_admin_product_update_rejects_explicit_null(field):
    with pytest.raises(ValidationError):
        AdminProductUpdate(**{field: None})


@pytest.mark.parametrize("price", [0, -1, math.inf, -math.inf, math.nan])
def test_admin_product_update_rejects_non_positive_or_non_finite_price(price):
    with pytest.raises(ValidationError):
        AdminProductUpdate(price=price)


def test_admin_product_update_enforces_lengths_and_unknown_fields():
    with pytest.raises(ValidationError):
        AdminProductUpdate(title="x" * 256)
    with pytest.raises(ValidationError):
        AdminProductUpdate(brand="x" * 121)
    with pytest.raises(ValidationError):
        AdminProductUpdate(category="x" * 121)
    with pytest.raises(ValidationError):
        AdminProductUpdate(description="x" * 20_001)
    with pytest.raises(ValidationError):
        AdminProductUpdate(provider_secret="should-not-be-accepted")
