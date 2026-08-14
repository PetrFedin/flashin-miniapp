from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base, utcnow_naive


class ProductMerchandising(Base):
    __tablename__ = "product_merchandising"
    __table_args__ = (
        UniqueConstraint("product_id", name="uq_product_merchandising_product"),
        CheckConstraint(
            "availability_status IN ('in_stock', 'preorder', 'made_to_order', 'out_of_stock')",
            name="ck_product_merchandising_availability",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"), index=True)
    availability_status: Mapped[str] = mapped_column(String(32), default="in_stock")
    material: Mapped[str] = mapped_column(String(255), default="")
    season: Mapped[str] = mapped_column(String(120), default="")
    badges_json: Mapped[str] = mapped_column(Text, default="[]")
    grid_rank: Mapped[int] = mapped_column(Integer, default=1000, index=True)
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
