from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field, field_validator

from .schemas import CheckoutIn


class DeliveryCheckoutIn(CheckoutIn):
    delivery_quote_id: str = Field(default="", max_length=80)


class DeliveryAddressIn(BaseModel):
    country_code: str = Field(default="RU", min_length=2, max_length=2)
    region: str = Field(default="", max_length=160)
    city: str = Field(default="", max_length=160)
    postal_code: str = Field(default="", max_length=32)
    address_line: str = Field(default="", max_length=500)

    @field_validator("country_code")
    @classmethod
    def normalize_country(cls, value: str) -> str:
        return str(value or "RU").strip().upper()


class DeliveryQuoteCreate(BaseModel):
    delivery_type: str = Field(default="pickup", max_length=64)
    address: DeliveryAddressIn | None = None


class DeliveryQuoteOut(BaseModel):
    public_id: str
    delivery_type: str
    address_snapshot: str
    zone_id: int | None = None
    provider_code: str
    service_code: str
    price: Decimal
    currency: str
    quote_version: int
    zone_version: int
    status: str
    expires_at: datetime
    model_config = {"from_attributes": True}


class DeliveryBookingReconcileIn(BaseModel):
    decision: str = Field(min_length=3, max_length=32)
    external_id: str = Field(default="", max_length=255)
    reason: str = Field(min_length=5, max_length=500)

    @field_validator("decision")
    @classmethod
    def normalize_decision(cls, value: str) -> str:
        normalized = str(value or "").strip().lower()
        if normalized not in {"confirmed", "not_booked"}:
            raise ValueError("decision must be confirmed or not_booked")
        return normalized

    @field_validator("external_id", "reason")
    @classmethod
    def normalize_text(cls, value: str) -> str:
        return " ".join(str(value or "").strip().split())


class DeliveryZoneAuthorityCreate(BaseModel):
    name: str = Field(min_length=2, max_length=255)
    delivery_type: str = Field(default="courier", max_length=64)
    price: Decimal = Field(default=Decimal("0.00"), ge=0)
    active: bool = True
    description: str = Field(default="", max_length=2000)
    priority: int = Field(default=100, ge=0, le=1_000_000)
    country_code: str = Field(default="RU", min_length=2, max_length=2)
    region: str = Field(default="", max_length=160)
    city: str = Field(default="", max_length=160)
    postal_prefix: str = Field(default="", max_length=32)
    provider_code: str = Field(default="courier", min_length=2, max_length=64)
    service_code: str = Field(default="courier", min_length=2, max_length=96)
    currency: str = Field(default="RUB", min_length=3, max_length=3)

    @field_validator("country_code", "currency")
    @classmethod
    def normalize_upper(cls, value: str) -> str:
        return str(value or "").strip().upper()


class DeliveryZoneAuthorityUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=255)
    price: Decimal | None = Field(default=None, ge=0)
    active: bool | None = None
    description: str | None = Field(default=None, max_length=2000)
    priority: int | None = Field(default=None, ge=0, le=1_000_000)
    country_code: str | None = Field(default=None, min_length=2, max_length=2)
    region: str | None = Field(default=None, max_length=160)
    city: str | None = Field(default=None, max_length=160)
    postal_prefix: str | None = Field(default=None, max_length=32)
    provider_code: str | None = Field(default=None, min_length=2, max_length=64)
    service_code: str | None = Field(default=None, min_length=2, max_length=96)
    currency: str | None = Field(default=None, min_length=3, max_length=3)

    @field_validator("country_code", "currency")
    @classmethod
    def normalize_optional_upper(cls, value: str | None) -> str | None:
        return None if value is None else str(value).strip().upper()
