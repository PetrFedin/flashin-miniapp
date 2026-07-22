from datetime import datetime

from pydantic import BaseModel, Field, model_validator


class ShowroomLocationCreate(BaseModel):
    name: str = Field(min_length=2, max_length=255)
    address: str = Field(min_length=3)
    city: str = Field(default="Москва", max_length=120)
    timezone: str = Field(default="Europe/Moscow", max_length=64)
    phone: str = Field(default="", max_length=64)
    slot_duration_minutes: int = Field(default=60, ge=15, le=240)
    opening_hours_json: str = "{}"
    active: bool = True


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
    showroom_note: str = ""


class ProductShowroomProfileOut(ProductShowroomProfileIn):
    id: int
    product_id: int
    updated_at: datetime
    model_config = {"from_attributes": True}


class ShowroomAppointmentCreate(BaseModel):
    product_id: int
    variant_id: int | None = None
    showroom_id: int | None = None
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
        if self.request_type not in {"fitting", "preorder", "preorder_fitting"}:
            raise ValueError("Unsupported appointment request type")
        if self.alternative_start and self.alternative_start == self.preferred_start:
            raise ValueError("Alternative time must differ from preferred time")
        return self


class ShowroomAppointmentUpdate(BaseModel):
    status: str | None = None
    showroom_id: int | None = None
    assigned_admin_id: int | None = None
    proposed_start: datetime | None = None
    confirmed_start: datetime | None = None
    duration_minutes: int | None = Field(default=None, ge=30, le=240)
    manager_note: str | None = Field(default=None, max_length=5000)


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
    request_type: str
    status: str
    preferred_start: datetime
    alternative_start: datetime | None = None
    proposed_start: datetime | None = None
    confirmed_start: datetime | None = None
    duration_minutes: int
    size: str
    color: str
    contact_phone: str
    customer_note: str
    manager_note: str
    source: str
    created_at: datetime
    updated_at: datetime
    messages: list[ShowroomAppointmentMessageOut] = []
    product_title: str = ""
    product_image_url: str = ""
    showroom_name: str = ""
    showroom_address: str = ""
    customer_name: str = ""
    customer_telegram_id: str = ""
    manager_email: str = ""
    model_config = {"from_attributes": True}


class ShowroomCustomerAction(BaseModel):
    action: str
    preferred_start: datetime | None = None
    alternative_start: datetime | None = None
    note: str = Field(default="", max_length=3000)
