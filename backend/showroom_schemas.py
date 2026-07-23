import json
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, Field, field_validator, model_validator


VALID_REQUEST_TYPES = {"fitting", "preorder", "preorder_fitting"}
VALID_APPOINTMENT_STATUSES = {
    "requested",
    "reviewing",
    "proposed",
    "confirmed",
    "checked_in",
    "fitting",
    "preordered",
    "purchased",
    "completed",
    "cancelled",
    "no_show",
}
VALID_AVAILABILITY = {"in_stock", "soon", "preorder", "unavailable"}
VALID_CUSTOMER_ACTIONS = {"accept_proposal", "request_reschedule", "cancel"}
VALID_WEEKDAYS = {"mon", "tue", "wed", "thu", "fri", "sat", "sun"}


def _validate_opening_hours(value: str) -> str:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ValueError("opening_hours_json must be valid JSON") from exc
    if not isinstance(parsed, dict):
        raise ValueError("opening_hours_json must be a JSON object")
    unknown_days = set(parsed) - VALID_WEEKDAYS
    if unknown_days:
        raise ValueError(f"Unsupported weekdays: {sorted(unknown_days)}")
    for day, intervals in parsed.items():
        if intervals in (None, []):
            continue
        if not isinstance(intervals, list):
            raise ValueError(f"Opening hours for {day} must be a list")
        for interval in intervals:
            if not isinstance(interval, list) or len(interval) != 2:
                raise ValueError(f"Opening interval for {day} must be [HH:MM, HH:MM]")
            for item in interval:
                if not isinstance(item, str) or len(item) != 5 or item[2] != ":":
                    raise ValueError(f"Invalid time in opening hours for {day}")
                hours, minutes = item.split(":")
                if not hours.isdigit() or not minutes.isdigit():
                    raise ValueError(f"Invalid time in opening hours for {day}")
                if not (0 <= int(hours) <= 23 and 0 <= int(minutes) <= 59):
                    raise ValueError(f"Invalid time in opening hours for {day}")
    return json.dumps(parsed, ensure_ascii=False, separators=(",", ":"))


class ShowroomLocationCreate(BaseModel):
    name: str = Field(min_length=2, max_length=255)
    address: str = Field(min_length=3)
    city: str = Field(default="Москва", max_length=120)
    timezone: str = Field(default="Europe/Moscow", max_length=64)
    phone: str = Field(default="", max_length=64)
    slot_duration_minutes: int = Field(default=60, ge=15, le=240)
    opening_hours_json: str = "{}"
    active: bool = True

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("Unknown IANA timezone") from exc
        return value

    @field_validator("opening_hours_json")
    @classmethod
    def validate_opening_hours(cls, value: str) -> str:
        return _validate_opening_hours(value)


class ShowroomLocationOut(ShowroomLocationCreate):
    id: int
    created_at: datetime
    updated_at: datetime
    model_config = {"from_attributes": True}


class ProductShowroomProfileIn(BaseModel):
    availability_status: str = "in_stock"
    preorder_enabled: bool = False
    fitting_enabled: bool = True
    expected_at: datetime | None = None
    showroom_note: str = Field(default="", max_length=5000)

    @field_validator("availability_status")
    @classmethod
    def validate_availability(cls, value: str) -> str:
        if value not in VALID_AVAILABILITY:
            raise ValueError(f"Availability must be one of {sorted(VALID_AVAILABILITY)}")
        return value

    @model_validator(mode="after")
    def validate_profile(self):
        if self.availability_status == "unavailable" and (self.fitting_enabled or self.preorder_enabled):
            raise ValueError("Unavailable product cannot allow fitting or preorder")
        if self.availability_status == "preorder" and not self.preorder_enabled:
            raise ValueError("Preorder availability requires preorder_enabled")
        return self


class ProductShowroomProfileOut(ProductShowroomProfileIn):
    id: int
    product_id: int
    updated_at: datetime
    model_config = {"from_attributes": True}


class ShowroomAppointmentCreate(BaseModel):
    product_id: int = Field(gt=0)
    variant_id: int | None = Field(default=None, gt=0)
    showroom_id: int | None = Field(default=None, gt=0)
    request_type: str = "fitting"
    preferred_start: datetime
    alternative_start: datetime | None = None
    duration_minutes: int = Field(default=60, ge=30, le=240)
    size: str = Field(default="", max_length=32)
    color: str = Field(default="", max_length=64)
    contact_phone: str = Field(default="", max_length=64)
    customer_note: str = Field(default="", max_length=3000)

    @model_validator(mode="after")
    def validate_request(self):
        if self.request_type not in VALID_REQUEST_TYPES:
            raise ValueError("Unsupported appointment request type")
        if self.alternative_start and self.alternative_start == self.preferred_start:
            raise ValueError("Alternative time must differ from preferred time")
        return self


class ShowroomAppointmentUpdate(BaseModel):
    status: str | None = None
    variant_id: int | None = Field(default=None, gt=0)
    showroom_id: int | None = Field(default=None, gt=0)
    assigned_admin_id: int | None = Field(default=None, gt=0)
    linked_order_id: int | None = Field(default=None, gt=0)
    proposed_start: datetime | None = None
    confirmed_start: datetime | None = None
    duration_minutes: int | None = Field(default=None, ge=30, le=240)
    manager_note: str | None = Field(default=None, max_length=5000)

    @field_validator("status")
    @classmethod
    def validate_status(cls, value: str | None) -> str | None:
        if value is not None and value not in VALID_APPOINTMENT_STATUSES:
            raise ValueError(f"Status must be one of {sorted(VALID_APPOINTMENT_STATUSES)}")
        return value


class ShowroomAppointmentMessageCreate(BaseModel):
    body: str = Field(min_length=1, max_length=5000)


class ShowroomAppointmentMessageOut(BaseModel):
    id: int
    appointment_id: int
    sender_type: str
    sender_customer_id: int | None = None
    sender_admin_id: int | None = None
    body: str
    created_at: datetime
    read_at: datetime | None = None
    model_config = {"from_attributes": True}


class ShowroomAppointmentOut(BaseModel):
    id: int
    customer_id: int
    product_id: int
    variant_id: int | None = None
    showroom_id: int | None = None
    assigned_admin_id: int | None = None
    linked_order_id: int | None = None
    request_type: str
    status: str
    preferred_start: datetime
    alternative_start: datetime | None = None
    proposed_start: datetime | None = None
    confirmed_start: datetime | None = None
    duration_minutes: int
    inventory_reserved: bool = False
    inventory_reserved_at: datetime | None = None
    inventory_released_at: datetime | None = None
    reservation_expires_at: datetime | None = None
    size: str
    color: str
    contact_phone: str
    customer_note: str
    manager_note: str
    source: str
    created_at: datetime
    updated_at: datetime
    messages: list[ShowroomAppointmentMessageOut] = Field(default_factory=list)
    product_title: str = ""
    product_image_url: str = ""
    showroom_name: str = ""
    showroom_address: str = ""
    customer_name: str = ""
    customer_telegram_id: str = ""
    manager_email: str = ""
    linked_order_status: str = ""
    linked_order_payment_status: str = ""
    model_config = {"from_attributes": True}


class ShowroomCustomerAction(BaseModel):
    action: str
    preferred_start: datetime | None = None
    alternative_start: datetime | None = None
    note: str = Field(default="", max_length=3000)

    @model_validator(mode="after")
    def validate_action(self):
        if self.action not in VALID_CUSTOMER_ACTIONS:
            raise ValueError("Unsupported customer action")
        if self.action == "request_reschedule" and self.preferred_start is None:
            raise ValueError("preferred_start is required for reschedule")
        return self
