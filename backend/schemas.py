import math
from datetime import datetime
from pydantic import BaseModel, Field, field_validator


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


class PromoApplyIn(BaseModel):
    code: str


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
    sku: str
    product_title: str


class AbandonedCartOut(BaseModel):
    cart_id: int
    customer_id: int
    telegram_id: str
    items_count: int
    total_amount: float



class SupportTicketCreate(BaseModel):
    order_id: int | None = None
    subject: str
    message: str
    priority: str = "normal"


class SupportTicketOut(BaseModel):
    id: int
    order_id: int | None = None
    subject: str
    message: str
    status: str
    priority: str
    model_config = {"from_attributes": True}


class SupportTicketUpdate(BaseModel):
    status: str | None = None
    priority: str | None = None
    assigned_admin_id: int | None = None


class PrivacyRequestCreate(BaseModel):
    request_type: str


class PrivacyRequestOut(BaseModel):
    id: int
    request_type: str
    status: str
    result_url: str = ""
    model_config = {"from_attributes": True}


class ConsentIn(BaseModel):
    consent_type: str
    granted: bool = True


class WebhookOutboxOut(BaseModel):
    id: int
    destination: str
    event_type: str
    status: str
    attempts: int
    last_error: str = ""
    model_config = {"from_attributes": True}



class MoySkladSyncOut(BaseModel):
    id: int
    sync_type: str
    status: str
    products_seen: int
    products_upserted: int
    variants_upserted: int
    error: str = ""
    model_config = {"from_attributes": True}


class CrmProfileOut(BaseModel):
    customer_id: int
    segment: str
    orders_count: int
    total_spent: float
    average_order_value: float
    loyalty_points: float
    vip: bool
    model_config = {"from_attributes": True}


class RecommendationOut(BaseModel):
    product_id: int
    recommended_product_id: int
    score: float
    source: str
    model_config = {"from_attributes": True}


class SizeHelperIn(BaseModel):
    height_cm: int | None = None
    weight_kg: int | None = None
    usual_size: str | None = None
    fit_preference: str = "regular"



class LoyaltyTransactionOut(BaseModel):
    id: int
    customer_id: int
    order_id: int | None = None
    points_delta: float
    reason: str
    model_config = {"from_attributes": True}


class ReferralCodeOut(BaseModel):
    id: int
    customer_id: int
    code: str
    reward_points: float
    used_count: int
    active: bool
    model_config = {"from_attributes": True}


class MarketingCampaignCreate(BaseModel):
    name: str
    segment: str = "all"
    message: str


class MarketingCampaignOut(BaseModel):
    id: int
    name: str
    segment: str
    message: str
    status: str
    sent_count: int
    model_config = {"from_attributes": True}


class ProductSearchOut(BaseModel):
    id: int
    title: str
    price: float
    currency: str
    image_url: str | None = None


class LookCreate(BaseModel):
    title: str
    description: str = ""
    product_ids: list[int] = []


class LookOut(BaseModel):
    id: int
    title: str
    description: str
    product_ids: list[int] = []


class CustomerTimelineOut(BaseModel):
    id: int
    customer_id: int
    event_type: str
    title: str
    payload: str
    created_at: datetime
    model_config = {"from_attributes": True}



class MoySkladMappingRuleCreate(BaseModel):
    source_field: str
    source_value: str
    target_field: str
    target_value: str
    active: bool = True


class MoySkladMappingRuleOut(BaseModel):
    id: int
    source_field: str
    source_value: str
    target_field: str
    target_value: str
    active: bool
    model_config = {"from_attributes": True}


class MoySkladConflictOut(BaseModel):
    id: int
    moysklad_id: str
    sku: str
    conflict_type: str
    message: str
    status: str
    model_config = {"from_attributes": True}


class LoyaltyRedeemIn(BaseModel):
    points: float


class ReferralApplyIn(BaseModel):
    code: str


class AdminProductUpdate(BaseModel):
    title: str | None = None
    description: str | None = None
    price: float | None = None
    category: str | None = None
    brand: str | None = None
    active: bool | None = None
    model_config = {"extra": "forbid"}

    @field_validator("title", "brand", "category")
    @classmethod
    def validate_required_text(cls, value: str | None, info):
        if value is None:
            raise ValueError(f"{info.field_name} cannot be null")
        cleaned = value.strip()
        limits = {"title": 255, "brand": 120, "category": 120}
        if not cleaned:
            raise ValueError(f"{info.field_name} cannot be blank")
        if len(cleaned) > limits[info.field_name]:
            raise ValueError(f"{info.field_name} is too long")
        return cleaned

    @field_validator("description")
    @classmethod
    def validate_description(cls, value: str | None):
        if value is None:
            raise ValueError("description cannot be null")
        cleaned = value.strip()
        if len(cleaned) > 20_000:
            raise ValueError("description is too long")
        return cleaned

    @field_validator("price")
    @classmethod
    def validate_price(cls, value: float | None):
        if value is None or not math.isfinite(value) or value <= 0:
            raise ValueError("price must be finite and positive")
        return round(value, 2)

    @field_validator("active")
    @classmethod
    def validate_active(cls, value: bool | None):
        if value is None:
            raise ValueError("active cannot be null")
        return value


class SearchRebuildOut(BaseModel):
    indexed: int
    engine: str



class StockReconciliationOut(BaseModel):
    id: int
    variant_id: int
    sku: str
    local_stock_qty: int
    external_stock_qty: int
    local_reserved_qty: int
    action: str
    status: str
    message: str
    model_config = {"from_attributes": True}


class CampaignScheduleIn(BaseModel):
    scheduled_at: datetime


class TimelineEventOut(BaseModel):
    id: int
    event_type: str
    title: str
    payload: str
    model_config = {"from_attributes": True}



class FulfillmentTaskOut(BaseModel):
    id: int
    order_id: int
    status: str
    assigned_admin_id: int | None = None
    comment: str = ""
    model_config = {"from_attributes": True}


class FulfillmentUpdateIn(BaseModel):
    status: str
    comment: str = ""


class SlaEventOut(BaseModel):
    id: int
    order_id: int
    event_type: str
    due_at: datetime
    status: str
    model_config = {"from_attributes": True}


class WebhookDestinationCreate(BaseModel):
    name: str
    url: str
    event_type: str = "*"
    active: bool = True
    signing_secret: str = ""


class WebhookDestinationOut(BaseModel):
    id: int
    name: str
    url: str
    event_type: str
    active: bool
    model_config = {"from_attributes": True}


class CustomerProfileOut(BaseModel):
    customer: dict
    crm: dict | None = None
    referral_code: str | None = None
    loyalty_points: float = 0



class FeatureFlagIn(BaseModel):
    key: str
    enabled: bool = True
    description: str = ""


class FeatureFlagOut(BaseModel):
    id: int
    key: str
    enabled: bool
    description: str
    model_config = {"from_attributes": True}


class RemoteConfigIn(BaseModel):
    key: str
    value_json: dict
    description: str = ""


class RemoteConfigOut(BaseModel):
    id: int
    key: str
    value_json: str
    description: str
    model_config = {"from_attributes": True}


class CmsPageIn(BaseModel):
    slug: str
    title: str
    content_json: dict = {}
    active: bool = True


class CmsPageOut(BaseModel):
    id: int
    slug: str
    title: str
    content_json: str
    active: bool
    model_config = {"from_attributes": True}


class CmsBlockIn(BaseModel):
    page_slug: str
    block_type: str
    title: str = ""
    payload_json: dict = {}
    sort_order: int = 0
    active: bool = True


class CmsBlockOut(BaseModel):
    id: int
    page_slug: str
    block_type: str
    title: str
    payload_json: str
    sort_order: int
    active: bool
    model_config = {"from_attributes": True}


class BusinessEventOut(BaseModel):
    id: int
    event_type: str
    aggregate_type: str
    aggregate_id: str
    status: str
    attempts: int
    model_config = {"from_attributes": True}


class AuditTrailOut(BaseModel):
    id: int
    admin_id: int | None = None
    action: str
    entity_type: str
    entity_id: str
    before_json: str
    after_json: str
    ip_address: str
    user_agent: str
    model_config = {"from_attributes": True}



class PaymentReconciliationOut(BaseModel):
    id: int
    order_id: int | None = None
    provider_payment_id: str
    local_status: str
    provider_status: str
    amount_local: float
    amount_provider: float
    status: str
    message: str
    model_config = {"from_attributes": True}


class DeliveryProviderIn(BaseModel):
    code: str
    name: str
    active: bool = True
    config_json: dict = {}


class DeliveryProviderOut(BaseModel):
    id: int
    code: str
    name: str
    active: bool
    config_json: str
    model_config = {"from_attributes": True}


class DeliveryShipmentOut(BaseModel):
    id: int
    order_id: int
    provider_code: str
    tracking_number: str
    status: str
    price: float
    model_config = {"from_attributes": True}


class AdminIpAllowlistIn(BaseModel):
    cidr: str
    description: str = ""
    active: bool = True


class AdminLoginEventOut(BaseModel):
    id: int
    email: str
    ip_address: str
    success: bool
    reason: str
    model_config = {"from_attributes": True}


class MoySkladSkuMatchOut(BaseModel):
    id: int
    local_variant_id: int
    moysklad_id: str
    external_sku: str
    confidence: float
    confirmed: bool
    model_config = {"from_attributes": True}
