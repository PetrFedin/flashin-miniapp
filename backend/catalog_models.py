from __future__ import annotations

from datetime import datetime
from urllib.parse import unquote, urlsplit

from sqlalchemy import Boolean, CheckConstraint, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint, event
from sqlalchemy.orm import Mapped, mapped_column

from .config import get_settings
from .database import Base, utcnow_naive
from .models import ProductImage


def managed_storage_key_from_url(url: str, public_base_url: str) -> str:
    """Return a safe managed object key only for URLs under our media base."""

    raw_url = str(url or "").strip()
    raw_base = str(public_base_url or "").strip().rstrip("/")
    if not raw_url or not raw_base:
        return ""
    url_parts = urlsplit(raw_url)
    base_parts = urlsplit(raw_base)
    if (
        url_parts.scheme not in {"http", "https"}
        or base_parts.scheme not in {"http", "https"}
        or url_parts.scheme.lower() != base_parts.scheme.lower()
        or url_parts.netloc.lower() != base_parts.netloc.lower()
    ):
        return ""
    base_path = base_parts.path.rstrip("/") + "/"
    if not url_parts.path.startswith(base_path):
        return ""
    key = unquote(url_parts.path[len(base_path):]).lstrip("/")
    parts = key.split("/")
    if not key or any(part in {"", ".", ".."} for part in parts):
        return ""
    return key


@event.listens_for(ProductImage, "before_insert")
@event.listens_for(ProductImage, "before_update")
def _restore_managed_product_image_storage_key(_mapper, _connection, target: ProductImage) -> None:
    if str(target.storage_key or "").strip():
        return
    target.storage_key = managed_storage_key_from_url(
        target.url,
        get_settings().media_public_base_url,
    )


class ProductMerchandising(Base):
    __tablename__ = "product_merchandising"
    __table_args__ = (
        UniqueConstraint("product_id", name="uq_product_merchandising_product"),
        CheckConstraint(
            "availability_status IN ('in_stock', 'preorder', 'made_to_order', 'out_of_stock')",
            name="ck_product_merchandising_availability",
        ),
        CheckConstraint(
            "promo_price IS NULL OR promo_price > 0",
            name="ck_product_merchandising_promo_price",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"), index=True)
    availability_status: Mapped[str] = mapped_column(String(32), default="in_stock")
    material: Mapped[str] = mapped_column(String(255), default="")
    season: Mapped[str] = mapped_column(String(120), default="")
    badges_json: Mapped[str] = mapped_column(Text, default="[]")
    grid_rank: Mapped[int] = mapped_column(Integer, default=1000, index=True)
    promo_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    sale_starts_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    sale_ends_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    showroom_fitting_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)


class ProductVideo(Base):
    __tablename__ = "product_videos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"), index=True)
    url: Mapped[str] = mapped_column(String(2048))
    title: Mapped[str] = mapped_column(String(255), default="")
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)


class ProductExternalAvailability(Base):
    __tablename__ = "product_external_availability"
    __table_args__ = (
        CheckConstraint(
            "availability_status IN ('in_stock', 'preorder', 'made_to_order', 'out_of_stock')",
            name="ck_product_external_availability_status",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"), index=True)
    source_name: Mapped[str] = mapped_column(String(255))
    url: Mapped[str] = mapped_column(String(2048))
    availability_status: Mapped[str] = mapped_column(String(32), default="in_stock")
    price: Mapped[float | None] = mapped_column(Float, nullable=True)
    currency: Mapped[str] = mapped_column(String(8), default="RUB")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)


class ProductFeedback(Base):
    __tablename__ = "product_feedback"
    __table_args__ = (
        UniqueConstraint("product_id", "customer_id", name="uq_product_feedback_customer"),
        CheckConstraint("rating >= 1 AND rating <= 5", name="ck_product_feedback_rating"),
        CheckConstraint(
            "status IN ('published', 'hidden')",
            name="ck_product_feedback_status",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"), index=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id", ondelete="CASCADE"), index=True)
    rating: Mapped[int] = mapped_column(Integer)
    comment: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(32), default="published")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)


class ShowroomAppointment(Base):
    __tablename__ = "showroom_appointments"
    __table_args__ = (
        CheckConstraint(
            "status IN ('requested', 'confirmed', 'cancelled', 'completed')",
            name="ck_showroom_appointments_status",
        ),
        CheckConstraint(
            "duration_minutes >= 15 AND duration_minutes <= 180",
            name="ck_showroom_appointments_duration",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id", ondelete="CASCADE"), index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"), index=True)
    starts_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    duration_minutes: Mapped[int] = mapped_column(Integer, default=30)
    status: Mapped[str] = mapped_column(String(32), default="requested", index=True)
    notes: Mapped[str] = mapped_column(Text, default="")
    active_slot_key: Mapped[str | None] = mapped_column(String(32), unique=True, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)
