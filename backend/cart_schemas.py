import math

from pydantic import BaseModel, Field, model_validator

from .schemas import CartItemOut


class CartOut(BaseModel):
    id: int
    items: list[CartItemOut]
    total_amount: float = Field(ge=0)
    delivery_price: float = Field(default=0, ge=0)
    discount_amount: float = Field(ge=0)
    promo_discount_amount: float = Field(ge=0)
    loyalty_points_reserved: int = Field(ge=0)
    loyalty_discount_amount: float = Field(ge=0)
    final_amount: float = Field(ge=0)
    promo_code: str | None = None

    @model_validator(mode="after")
    def validate_pricing_breakdown(self):
        numeric_values = (
            self.total_amount,
            self.delivery_price,
            self.discount_amount,
            self.promo_discount_amount,
            self.loyalty_discount_amount,
            self.final_amount,
        )
        if any(not math.isfinite(value) for value in numeric_values):
            raise ValueError("Cart pricing values must be finite")

        expected_discount = round(self.promo_discount_amount + self.loyalty_discount_amount, 2)
        if abs(self.discount_amount - expected_discount) > 0.01:
            raise ValueError("Cart discount does not match its pricing breakdown")

        expected_final = round(max(self.total_amount - self.discount_amount, 0), 2)
        if abs(self.final_amount - expected_final) > 0.01:
            raise ValueError("Cart final amount does not match pricing breakdown")
        return self
