from datetime import datetime
from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from .database import Base


class AdminUser(Base):
    __tablename__ = "admin_users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(64), default="manager")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Customer(Base):
    __tablename__ = "customers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    telegram_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    username: Mapped[str] = mapped_column(String(255), default="")
    first_name: Mapped[str] = mapped_column(String(255), default="")
    last_name: Mapped[str] = mapped_column(String(255), default="")
    phone: Mapped[str] = mapped_column(String(64), default="")
    email: Mapped[str] = mapped_column(String(255), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    carts: Mapped[list["Cart"]] = relationship(back_populates="customer")
    orders: Mapped[list["Order"]] = relationship(back_populates="customer")
    wishlist_items: Mapped[list["WishlistItem"]] = relationship(back_populates="customer")


class Product(Base):
    __tablename__ = "products"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    sku: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    moysklad_id: Mapped[str] = mapped_column(String(255), default="", index=True)
    title: Mapped[str] = mapped_column(String(255), index=True)
    slug: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    brand: Mapped[str] = mapped_column(String(120), default="FLASHIN")
    description: Mapped[str] = mapped_column(Text, default="")
    price: Mapped[float] = mapped_column(Float)
    old_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    currency: Mapped[str] = mapped_column(String(8), default="RUB")
    category: Mapped[str] = mapped_column(String(120), default="Clothing")
    gender: Mapped[str] = mapped_column(String(32), default="unisex")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_drop: Mapped[bool] = mapped_column(Boolean, default=False)
    is_rare: Mapped[bool] = mapped_column(Boolean, default=False)
    drop_starts_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    vip_only_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    images: Mapped[list["ProductImage"]] = relationship(back_populates="product", cascade="all, delete-orphan")
    variants: Mapped[list["ProductVariant"]] = relationship(back_populates="product", cascade="all, delete-orphan")


class ProductImage(Base):
    __tablename__ = "product_images"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), index=True)
    url: Mapped[str] = mapped_column(String(2048))
    storage_key: Mapped[str] = mapped_column(String(1024), default="")
    sort_order: Mapped[int] = mapped_column(Integer, default=0)

    product: Mapped[Product] = relationship(back_populates="images")


class MediaAsset(Base):
    __tablename__ = "media_assets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    url: Mapped[str] = mapped_column(String(2048))
    storage_key: Mapped[str] = mapped_column(String(1024), index=True)
    filename: Mapped[str] = mapped_column(String(255))
    content_type: Mapped[str] = mapped_column(String(120), default="")
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class ProductVariant(Base):
    __tablename__ = "product_variants"
    __table_args__ = (UniqueConstraint("product_id", "size", "color", name="uq_product_size_color"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), index=True)
    size: Mapped[str] = mapped_column(String(32), index=True)
    color: Mapped[str] = mapped_column(String(64), default="")
    sku: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    moysklad_id: Mapped[str] = mapped_column(String(255), default="", index=True)
    stock_qty: Mapped[int] = mapped_column(Integer, default=0)
    reserved_qty: Mapped[int] = mapped_column(Integer, default=0)

    product: Mapped[Product] = relationship(back_populates="variants")

    @property
    def available_qty(self) -> int:
        return max(self.stock_qty - self.reserved_qty, 0)


class Cart(Base):
    __tablename__ = "carts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), index=True)
    status: Mapped[str] = mapped_column(String(32), default="active")
    promo_code_id: Mapped[int | None] = mapped_column(ForeignKey("promo_codes.id"), nullable=True)
    referral_code: Mapped[str] = mapped_column(String(64), default="")
    loyalty_points_to_redeem: Mapped[float] = mapped_column(Float, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    abandoned_notified_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    customer: Mapped[Customer] = relationship(back_populates="carts")
    items: Mapped[list["CartItem"]] = relationship(back_populates="cart", cascade="all, delete-orphan")
    promo_code: Mapped["PromoCode"] = relationship()


class CartItem(Base):
    __tablename__ = "cart_items"
    __table_args__ = (UniqueConstraint("cart_id", "variant_id", name="uq_cart_variant"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    cart_id: Mapped[int] = mapped_column(ForeignKey("carts.id"), index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), index=True)
    variant_id: Mapped[int] = mapped_column(ForeignKey("product_variants.id"), index=True)
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    cart: Mapped[Cart] = relationship(back_populates="items")
    product: Mapped[Product] = relationship()
    variant: Mapped[ProductVariant] = relationship()


class PromoCode(Base):
    __tablename__ = "promo_codes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    discount_type: Mapped[str] = mapped_column(String(32), default="percent")
    discount_value: Mapped[float] = mapped_column(Float)
    min_amount: Mapped[float] = mapped_column(Float, default=0)
    max_uses: Mapped[int] = mapped_column(Integer, default=0)
    used_count: Mapped[int] = mapped_column(Integer, default=0)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), index=True)
    promo_code_id: Mapped[int | None] = mapped_column(ForeignKey("promo_codes.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(64), default="created")
    payment_status: Mapped[str] = mapped_column(String(64), default="pending")
    delivery_status: Mapped[str] = mapped_column(String(64), default="not_started")
    total_amount: Mapped[float] = mapped_column(Float, default=0)
    delivery_price: Mapped[float] = mapped_column(Float, default=0)
    discount_amount: Mapped[float] = mapped_column(Float, default=0)
    loyalty_points_redeemed: Mapped[float] = mapped_column(Float, default=0)
    loyalty_discount_amount: Mapped[float] = mapped_column(Float, default=0)
    referral_code: Mapped[str] = mapped_column(String(64), default="")
    currency: Mapped[str] = mapped_column(String(8), default="RUB")
    delivery_type: Mapped[str] = mapped_column(String(64), default="pickup")
    address: Mapped[str] = mapped_column(Text, default="")
    comment: Mapped[str] = mapped_column(Text, default="")
    tracking_number: Mapped[str] = mapped_column(String(255), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    customer: Mapped[Customer] = relationship(back_populates="orders")
    promo_code: Mapped[PromoCode] = relationship()
    items: Mapped[list["OrderItem"]] = relationship(back_populates="order", cascade="all, delete-orphan")
    payments: Mapped[list["Payment"]] = relationship(back_populates="order")
    returns: Mapped[list["ReturnRequest"]] = relationship(back_populates="order")


class OrderItem(Base):
    __tablename__ = "order_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), index=True)
    variant_id: Mapped[int] = mapped_column(ForeignKey("product_variants.id"), index=True)
    title: Mapped[str] = mapped_column(String(255))
    size: Mapped[str] = mapped_column(String(32), default="")
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    price: Mapped[float] = mapped_column(Float)

    order: Mapped[Order] = relationship(back_populates="items")


class Payment(Base):
    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), index=True)
    provider: Mapped[str] = mapped_column(String(64), default="yookassa")
    provider_payment_id: Mapped[str] = mapped_column(String(255), default="", index=True)
    status: Mapped[str] = mapped_column(String(64), default="pending")
    amount: Mapped[float] = mapped_column(Float)
    confirmation_url: Mapped[str] = mapped_column(String(2048), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    order: Mapped[Order] = relationship(back_populates="payments")


class PaymentEvent(Base):
    __tablename__ = "payment_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    provider: Mapped[str] = mapped_column(String(64), default="yookassa")
    provider_payment_id: Mapped[str] = mapped_column(String(255), index=True)
    event_type: Mapped[str] = mapped_column(String(120), index=True)
    raw_payload: Mapped[str] = mapped_column(Text)
    processed: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Notification(Base):
    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    telegram_id: Mapped[str] = mapped_column(String(64), index=True)
    message: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default="pending")
    error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class ReturnRequest(Base):
    __tablename__ = "return_requests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), index=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), index=True)
    reason: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(64), default="requested")
    provider_refund_id: Mapped[str] = mapped_column(String(255), default="")
    refund_amount: Mapped[float] = mapped_column(Float, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    order: Mapped[Order] = relationship(back_populates="returns")


class WishlistItem(Base):
    __tablename__ = "wishlist_items"
    __table_args__ = (UniqueConstraint("customer_id", "product_id", name="uq_customer_product_wishlist"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    customer: Mapped[Customer] = relationship(back_populates="wishlist_items")
    product: Mapped[Product] = relationship()


class RestockSubscription(Base):
    __tablename__ = "restock_subscriptions"
    __table_args__ = (UniqueConstraint("customer_id", "variant_id", name="uq_customer_variant_restock"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), index=True)
    variant_id: Mapped[int] = mapped_column(ForeignKey("product_variants.id"), index=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class DeliveryZone(Base):
    __tablename__ = "delivery_zones"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), unique=True)
    delivery_type: Mapped[str] = mapped_column(String(64), default="courier")
    price: Mapped[float] = mapped_column(Float, default=0)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    description: Mapped[str] = mapped_column(Text, default="")


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    admin_id: Mapped[int | None] = mapped_column(ForeignKey("admin_users.id"), nullable=True)
    action: Mapped[str] = mapped_column(String(120), index=True)
    entity_type: Mapped[str] = mapped_column(String(120), default="")
    entity_id: Mapped[str] = mapped_column(String(120), default="")
    payload: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class InventoryAdjustment(Base):
    __tablename__ = "inventory_adjustments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    variant_id: Mapped[int] = mapped_column(ForeignKey("product_variants.id"), index=True)
    old_stock_qty: Mapped[int] = mapped_column(Integer)
    new_stock_qty: Mapped[int] = mapped_column(Integer)
    reason: Mapped[str] = mapped_column(String(255), default="")
    admin_id: Mapped[int | None] = mapped_column(ForeignKey("admin_users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class InventorySnapshot(Base):
    __tablename__ = "inventory_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    variant_id: Mapped[int] = mapped_column(ForeignKey("product_variants.id"), index=True)
    stock_qty: Mapped[int] = mapped_column(Integer)
    reserved_qty: Mapped[int] = mapped_column(Integer)
    source: Mapped[str] = mapped_column(String(64), default="system")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class AdminRolePermission(Base):
    __tablename__ = "admin_role_permissions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    role: Mapped[str] = mapped_column(String(64), index=True)
    permission: Mapped[str] = mapped_column(String(120), index=True)


class SupportTicket(Base):
    __tablename__ = "support_tickets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    customer_id: Mapped[int | None] = mapped_column(ForeignKey("customers.id"), nullable=True)
    order_id: Mapped[int | None] = mapped_column(ForeignKey("orders.id"), nullable=True)
    subject: Mapped[str] = mapped_column(String(255))
    message: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(64), default="open")
    priority: Mapped[str] = mapped_column(String(32), default="normal")
    assigned_admin_id: Mapped[int | None] = mapped_column(ForeignKey("admin_users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class WebhookOutbox(Base):
    __tablename__ = "webhook_outbox"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    destination: Mapped[str] = mapped_column(String(255))
    event_type: Mapped[str] = mapped_column(String(120), index=True)
    payload: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(64), default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str] = mapped_column(Text, default="")
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class PrivacyRequest(Base):
    __tablename__ = "privacy_requests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), index=True)
    request_type: Mapped[str] = mapped_column(String(64))  # export | delete | consent_withdrawal
    status: Mapped[str] = mapped_column(String(64), default="requested")
    result_url: Mapped[str] = mapped_column(String(2048), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class ConsentRecord(Base):
    __tablename__ = "consent_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), index=True)
    consent_type: Mapped[str] = mapped_column(String(120))
    granted: Mapped[bool] = mapped_column(Boolean, default=True)
    source: Mapped[str] = mapped_column(String(120), default="telegram_mini_app")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class MoySkladSyncLog(Base):
    __tablename__ = "moysklad_sync_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sync_type: Mapped[str] = mapped_column(String(64), default="manual")
    status: Mapped[str] = mapped_column(String(64), default="started")
    products_seen: Mapped[int] = mapped_column(Integer, default=0)
    products_upserted: Mapped[int] = mapped_column(Integer, default=0)
    variants_upserted: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class CrmProfile(Base):
    __tablename__ = "crm_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), unique=True, index=True)
    segment: Mapped[str] = mapped_column(String(120), default="new")
    orders_count: Mapped[int] = mapped_column(Integer, default=0)
    total_spent: Mapped[float] = mapped_column(Float, default=0)
    average_order_value: Mapped[float] = mapped_column(Float, default=0)
    last_order_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    loyalty_points: Mapped[float] = mapped_column(Float, default=0)
    vip: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class ProductRecommendation(Base):
    __tablename__ = "product_recommendations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), index=True)
    recommended_product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), index=True)
    score: Mapped[float] = mapped_column(Float, default=1)
    source: Mapped[str] = mapped_column(String(64), default="manual")


class LoyaltyTransaction(Base):
    __tablename__ = "loyalty_transactions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), index=True)
    order_id: Mapped[int | None] = mapped_column(ForeignKey("orders.id"), nullable=True)
    points_delta: Mapped[float] = mapped_column(Float)
    reason: Mapped[str] = mapped_column(String(255), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class ReferralCode(Base):
    __tablename__ = "referral_codes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), index=True)
    code: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    reward_points: Mapped[float] = mapped_column(Float, default=500)
    used_count: Mapped[int] = mapped_column(Integer, default=0)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class MarketingCampaign(Base):
    __tablename__ = "marketing_campaigns"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    segment: Mapped[str] = mapped_column(String(120), default="all")
    message: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(64), default="draft")
    sent_count: Mapped[int] = mapped_column(Integer, default=0)
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class ProductSearchIndex(Base):
    __tablename__ = "product_search_index"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), unique=True, index=True)
    search_text: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Look(Base):
    __tablename__ = "looks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(255))
    description: Mapped[str] = mapped_column(Text, default="")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class LookItem(Base):
    __tablename__ = "look_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    look_id: Mapped[int] = mapped_column(ForeignKey("looks.id"), index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), index=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)


class CustomerTimelineEvent(Base):
    __tablename__ = "customer_timeline_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), index=True)
    event_type: Mapped[str] = mapped_column(String(120), index=True)
    title: Mapped[str] = mapped_column(String(255))
    payload: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class MoySkladMappingRule(Base):
    __tablename__ = "moysklad_mapping_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_field: Mapped[str] = mapped_column(String(120))
    source_value: Mapped[str] = mapped_column(String(255))
    target_field: Mapped[str] = mapped_column(String(120))
    target_value: Mapped[str] = mapped_column(String(255))
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class MoySkladConflict(Base):
    __tablename__ = "moysklad_conflicts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    moysklad_id: Mapped[str] = mapped_column(String(255), index=True)
    sku: Mapped[str] = mapped_column(String(120), default="")
    conflict_type: Mapped[str] = mapped_column(String(120))
    message: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(64), default="open")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class ReferralAttribution(Base):
    __tablename__ = "referral_attributions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    referral_code_id: Mapped[int] = mapped_column(ForeignKey("referral_codes.id"), index=True)
    invited_customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(64), default="pending")
    rewarded_order_id: Mapped[int | None] = mapped_column(ForeignKey("orders.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    rewarded_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class StockReconciliationLog(Base):
    __tablename__ = "stock_reconciliation_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    variant_id: Mapped[int] = mapped_column(ForeignKey("product_variants.id"), index=True)
    sku: Mapped[str] = mapped_column(String(120), default="")
    local_stock_qty: Mapped[int] = mapped_column(Integer)
    external_stock_qty: Mapped[int] = mapped_column(Integer)
    local_reserved_qty: Mapped[int] = mapped_column(Integer, default=0)
    action: Mapped[str] = mapped_column(String(64), default="report")
    status: Mapped[str] = mapped_column(String(64), default="open")
    message: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class LoyaltyRedemptionHold(Base):
    __tablename__ = "loyalty_redemption_holds"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), index=True)
    cart_id: Mapped[int | None] = mapped_column(ForeignKey("carts.id"), nullable=True)
    order_id: Mapped[int | None] = mapped_column(ForeignKey("orders.id"), nullable=True)
    points: Mapped[float] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(64), default="reserved")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    released_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class FulfillmentTask(Base):
    __tablename__ = "fulfillment_tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), index=True)
    status: Mapped[str] = mapped_column(String(64), default="new")
    assigned_admin_id: Mapped[int | None] = mapped_column(ForeignKey("admin_users.id"), nullable=True)
    pick_started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    packed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    ready_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    comment: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class FulfillmentTaskItem(Base):
    __tablename__ = "fulfillment_task_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("fulfillment_tasks.id"), index=True)
    order_item_id: Mapped[int] = mapped_column(ForeignKey("order_items.id"), index=True)
    status: Mapped[str] = mapped_column(String(64), default="to_pick")
    picked_qty: Mapped[int] = mapped_column(Integer, default=0)
    issue: Mapped[str] = mapped_column(Text, default="")


class SlaEvent(Base):
    __tablename__ = "sla_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), index=True)
    event_type: Mapped[str] = mapped_column(String(120), index=True)
    due_at: Mapped[datetime]
    status: Mapped[str] = mapped_column(String(64), default="open")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class WebhookDestination(Base):
    __tablename__ = "webhook_destinations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    url: Mapped[str] = mapped_column(String(2048))
    event_type: Mapped[str] = mapped_column(String(120), default="*")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    signing_secret: Mapped[str] = mapped_column(String(255), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class FeatureFlag(Base):
    __tablename__ = "feature_flags"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    description: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class RemoteConfig(Base):
    __tablename__ = "remote_configs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    value_json: Mapped[str] = mapped_column(Text, default="{}")
    description: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class CmsPage(Base):
    __tablename__ = "cms_pages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slug: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(255))
    content_json: Mapped[str] = mapped_column(Text, default="{}")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class CmsBlock(Base):
    __tablename__ = "cms_blocks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    page_slug: Mapped[str] = mapped_column(String(255), index=True)
    block_type: Mapped[str] = mapped_column(String(120))
    title: Mapped[str] = mapped_column(String(255), default="")
    payload_json: Mapped[str] = mapped_column(Text, default="{}")
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class BusinessEvent(Base):
    __tablename__ = "business_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_type: Mapped[str] = mapped_column(String(120), index=True)
    aggregate_type: Mapped[str] = mapped_column(String(120), default="")
    aggregate_id: Mapped[str] = mapped_column(String(120), default="")
    payload_json: Mapped[str] = mapped_column(Text, default="{}")
    status: Mapped[str] = mapped_column(String(64), default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class AuditTrail(Base):
    __tablename__ = "audit_trails"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    admin_id: Mapped[int | None] = mapped_column(ForeignKey("admin_users.id"), nullable=True)
    action: Mapped[str] = mapped_column(String(120), index=True)
    entity_type: Mapped[str] = mapped_column(String(120), default="")
    entity_id: Mapped[str] = mapped_column(String(120), default="")
    before_json: Mapped[str] = mapped_column(Text, default="{}")
    after_json: Mapped[str] = mapped_column(Text, default="{}")
    ip_address: Mapped[str] = mapped_column(String(120), default="")
    user_agent: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class MediaDerivative(Base):
    __tablename__ = "media_derivatives"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    media_asset_id: Mapped[int] = mapped_column(ForeignKey("media_assets.id"), index=True)
    derivative_type: Mapped[str] = mapped_column(String(120))
    url: Mapped[str] = mapped_column(String(2048))
    storage_key: Mapped[str] = mapped_column(String(1024), default="")
    width: Mapped[int] = mapped_column(Integer, default=0)
    height: Mapped[int] = mapped_column(Integer, default=0)
    content_type: Mapped[str] = mapped_column(String(120), default="")
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class ScheduledJob(Base):
    __tablename__ = "scheduled_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_key: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    cron: Mapped[str] = mapped_column(String(120), default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_status: Mapped[str] = mapped_column(String(64), default="")


class AdminLoginEvent(Base):
    __tablename__ = "admin_login_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    admin_id: Mapped[int | None] = mapped_column(ForeignKey("admin_users.id"), nullable=True)
    email: Mapped[str] = mapped_column(String(255), default="")
    ip_address: Mapped[str] = mapped_column(String(120), default="")
    user_agent: Mapped[str] = mapped_column(Text, default="")
    success: Mapped[bool] = mapped_column(Boolean, default=False)
    reason: Mapped[str] = mapped_column(String(255), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class AdminSession(Base):
    __tablename__ = "admin_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    admin_id: Mapped[int] = mapped_column(ForeignKey("admin_users.id"), index=True)
    session_token_hash: Mapped[str] = mapped_column(String(255), index=True)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)
    ip_address: Mapped[str] = mapped_column(String(120), default="")
    user_agent: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class AdminPasswordReset(Base):
    __tablename__ = "admin_password_resets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    admin_id: Mapped[int] = mapped_column(ForeignKey("admin_users.id"), index=True)
    token_hash: Mapped[str] = mapped_column(String(255), index=True)
    used: Mapped[bool] = mapped_column(Boolean, default=False)
    expires_at: Mapped[datetime]
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class AdminTotpSecret(Base):
    __tablename__ = "admin_totp_secrets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    admin_id: Mapped[int] = mapped_column(ForeignKey("admin_users.id"), unique=True, index=True)
    secret: Mapped[str] = mapped_column(String(255))
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class AdminIpAllowlist(Base):
    __tablename__ = "admin_ip_allowlist"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    cidr: Mapped[str] = mapped_column(String(120), unique=True)
    description: Mapped[str] = mapped_column(Text, default="")
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class PaymentReconciliation(Base):
    __tablename__ = "payment_reconciliations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    payment_id: Mapped[int | None] = mapped_column(ForeignKey("payments.id"), nullable=True)
    order_id: Mapped[int | None] = mapped_column(ForeignKey("orders.id"), nullable=True)
    provider_payment_id: Mapped[str] = mapped_column(String(255), index=True, default="")
    local_status: Mapped[str] = mapped_column(String(64), default="")
    provider_status: Mapped[str] = mapped_column(String(64), default="")
    amount_local: Mapped[float] = mapped_column(Float, default=0)
    amount_provider: Mapped[float] = mapped_column(Float, default=0)
    status: Mapped[str] = mapped_column(String(64), default="open")
    message: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class DeliveryProvider(Base):
    __tablename__ = "delivery_providers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    config_json: Mapped[str] = mapped_column(Text, default="{}")


class DeliveryShipment(Base):
    __tablename__ = "delivery_shipments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), index=True)
    provider_code: Mapped[str] = mapped_column(String(64), default="courier")
    tracking_number: Mapped[str] = mapped_column(String(255), default="")
    status: Mapped[str] = mapped_column(String(64), default="created")
    price: Mapped[float] = mapped_column(Float, default=0)
    raw_payload: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class MoySkladSkuMatch(Base):
    __tablename__ = "moysklad_sku_matches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    local_variant_id: Mapped[int] = mapped_column(ForeignKey("product_variants.id"), index=True)
    moysklad_id: Mapped[str] = mapped_column(String(255), index=True)
    external_sku: Mapped[str] = mapped_column(String(120), default="")
    confidence: Mapped[float] = mapped_column(Float, default=1)
    confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class MediaProcessingJob(Base):
    __tablename__ = "media_processing_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    media_asset_id: Mapped[int] = mapped_column(ForeignKey("media_assets.id"), index=True)
    status: Mapped[str] = mapped_column(String(64), default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class AnalyticsEvent(Base):
    __tablename__ = "analytics_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    customer_id: Mapped[int | None] = mapped_column(ForeignKey("customers.id"), nullable=True)
    event_type: Mapped[str] = mapped_column(String(120), index=True)
    payload: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
