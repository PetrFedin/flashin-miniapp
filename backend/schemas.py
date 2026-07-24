from datetime import datetime
from pydantic import BaseModel, Field


class ImageOut(BaseModel):
    id: int
    url: str
    storage_key: str = ""
    sort_order: int
    model_config = {"from_attributes": True}


class VariantOut(BaseModel):
    id: int
    size: str
    color: str = ""
    sku: str
    stock_qty: int
    reserved_qty: int
    available_qty: int
    model_config = {"from_attributes": True}


class ProductOut(BaseModel):
    id: int
    sku: str
    title: str
    slug: str
    brand: str
    description: str = ""
    price: float
    old_price: float | None = None
    currency: str
    category: str
    gender: str
    active: bool
    is_drop: bool
    is_rare: bool
    drop_starts_at: datetime | None = None
    vip_only_until: datetime | None = None
    images: list[ImageOut] = []
    variants: list[VariantOut] = []
    model_config = {"from_attributes": True}


class ProductCreate(BaseModel):
    sku: str
    title: str
    slug: str
    brand: str = "FLASHIN"
    description: str = ""
    price: float
    old_price: float | None = None
    currency: str = "RUB"
    category: str = "Clothing"
    gender: str = "unisex"
    active: bool = True
    is_drop: bool = False
    is_rare: bool = False
    drop_starts_at: datetime | None = None
    vip_only_until: datetime | None = None
    images: list[str] = Field(default_factory=list)
    variants: list[dict] = Field(default_factory=list)


class ProductUpdate(ProductCreate):
    pass


class TelegramAuthIn(BaseModel):
    init_data: str


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"


class AdminLoginIn(BaseModel):
    email: str
    password: str


class MeOut(BaseModel):
    id: int
    telegram_id: str
    username: str = ""
    first_name: str = ""
    phone: str = ""


class CartAddIn(BaseModel):
    product_id: int
    variant_id: int
    quantity: int = Field(default=1, ge=1, le=10)


class CartQuantityIn(BaseModel):
    quantity: int = Field(ge=0, le=10)


class PromoApplyIn(BaseModel):
    code: str = Field(min_length=1, max_length=64)


class CartItemOut(BaseModel):
    id: int
    product_id: int
    variant_id: int
    title: str
    size: str
    quantity: int
    price: float
    available_qty: int


class CartOut(BaseModel):
    id: int
    items: list[CartItemOut]
    total_amount: float
    delivery_price: float = 0
    discount_amount: float = 0
    final_amount: float = 0
    promo_code: str | None = None


class CheckoutIn(BaseModel):
    name: str
    phone: str
    delivery_type: str = "pickup"
    address: str = ""
    comment: str = ""


class OrderStatusUpdate(BaseModel):
    status: str | None = None
    delivery_status: str | None = None
    tracking_number: str | None = None


class OrderItemOut(BaseModel):
    id: int
    product_id: int
    variant_id: int
    title: str
    size: str
    quantity: int
    price: float
    model_config = {"from_attributes": True}


class OrderOut(BaseModel):
    id: int
    status: str
    payment_status: str
    delivery_status: str
    total_amount: float
    delivery_price: float = 0
    discount_amount: float
    currency: str
    delivery_type: str
    address: str
    comment: str
    tracking_number: str = ""
    items: list[OrderItemOut] = []
    model_config = {"from_attributes": True}


class PaymentCreate(BaseModel):
    order_id: int


class PaymentOut(BaseModel):
    order_id: int
    provider: str
    status: str
    confirmation_url: str
    provider_payment_id: str = ""


class AnalyticsEventIn(BaseModel):
    event_type: str
    payload: dict = Field(default_factory=dict)


class PromoCodeCreate(BaseModel):
    code: str
    discount_type: str = "percent"
    discount_value: float
    min_amount: float = 0
    max_uses: int = 0
    active: bool = True
    expires_at: datetime | None = None


class ReturnCreate(BaseModel):
    order_id: int
    reason: str


class ReturnOut(BaseModel):
    id: int
    order_id: int
    reason: str
    status: str
    provider_refund_id: str = ""
    refund_amount: float = 0
    model_config = {"from_attributes": True}


class WishlistIn(BaseModel):
    product_id: int


class RestockSubscribeIn(BaseModel):
    variant_id: int


class MediaOut(BaseModel):
    id: int
    url: str
    storage_key: str
    filename: str
    content_type: str
    size_bytes: int
    model_config = {"from_attributes": True}


class DeliveryZoneCreate(BaseModel):
    name: str
    delivery_type: str = "courier"
    price: float = 0
    active: bool = True
    description: str = ""


class RefundApproveIn(BaseModel):
    return_id: int
    amount: float | None = None


class AuditLogOut(BaseModel):
    id: int
    admin_id: int | None = None
    action: str
    entity_type: str = ""
    entity_id: str = ""
    payload: str = "{}"
    model_config = {"from_attributes": True}


class InventoryAdjustmentIn(BaseModel):
    variant_id: int
    new_stock_qty: int
    reason: str = ""


class InventorySnapshotOut(BaseModel):
    variant_id: int
    stock_qty: int
    reserved_qty: int
    available_qty: int
