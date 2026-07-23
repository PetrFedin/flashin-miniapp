from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


class ProductShowroomProfile(Base):
    __tablename__ = "product_showroom_profiles"
    __table_args__ = (UniqueConstraint("product_id", name="uq_product_showroom_profile"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"), index=True)
    availability_status: Mapped[str] = mapped_column(String(32), default="in_stock", index=True)
    preorder_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    fitting_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    expected_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    showroom_note: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    product = relationship("Product")


class ShowroomLocation(Base):
    __tablename__ = "showroom_locations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    address: Mapped[str] = mapped_column(Text, default="")
    city: Mapped[str] = mapped_column(String(120), default="Москва")
    timezone: Mapped[str] = mapped_column(String(64), default="Europe/Moscow")
    phone: Mapped[str] = mapped_column(String(64), default="")
    slot_duration_minutes: Mapped[int] = mapped_column(Integer, default=60)
    opening_hours_json: Mapped[str] = mapped_column(Text, default="{}")
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class ShowroomAppointment(Base):
    __tablename__ = "showroom_appointments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), index=True)
    variant_id: Mapped[int | None] = mapped_column(ForeignKey("product_variants.id"), nullable=True, index=True)
    showroom_id: Mapped[int | None] = mapped_column(ForeignKey("showroom_locations.id"), nullable=True, index=True)
    assigned_admin_id: Mapped[int | None] = mapped_column(ForeignKey("admin_users.id"), nullable=True, index=True)
    linked_order_id: Mapped[int | None] = mapped_column(ForeignKey("orders.id"), nullable=True, index=True)

    request_type: Mapped[str] = mapped_column(String(32), default="fitting", index=True)
    status: Mapped[str] = mapped_column(String(32), default="requested", index=True)
    preferred_start: Mapped[datetime] = mapped_column(DateTime, index=True)
    alternative_start: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    proposed_start: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    confirmed_start: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    duration_minutes: Mapped[int] = mapped_column(Integer, default=60)

    inventory_reserved: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    inventory_reserved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    inventory_released_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    reservation_expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)

    size: Mapped[str] = mapped_column(String(32), default="")
    color: Mapped[str] = mapped_column(String(64), default="")
    contact_phone: Mapped[str] = mapped_column(String(64), default="")
    customer_note: Mapped[str] = mapped_column(Text, default="")
    manager_note: Mapped[str] = mapped_column(Text, default="")
    source: Mapped[str] = mapped_column(String(64), default="telegram_mini_app")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    customer = relationship("Customer")
    product = relationship("Product")
    variant = relationship("ProductVariant")
    showroom = relationship("ShowroomLocation")
    assigned_admin = relationship("AdminUser")
    linked_order = relationship("Order")
    messages: Mapped[list["ShowroomAppointmentMessage"]] = relationship(
        back_populates="appointment",
        cascade="all, delete-orphan",
        order_by="ShowroomAppointmentMessage.created_at",
    )


class ShowroomAppointmentMessage(Base):
    __tablename__ = "showroom_appointment_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    appointment_id: Mapped[int] = mapped_column(
        ForeignKey("showroom_appointments.id", ondelete="CASCADE"),
        index=True,
    )
    sender_type: Mapped[str] = mapped_column(String(24), index=True)
    sender_customer_id: Mapped[int | None] = mapped_column(ForeignKey("customers.id"), nullable=True)
    sender_admin_id: Mapped[int | None] = mapped_column(ForeignKey("admin_users.id"), nullable=True)
    body: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    read_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    appointment: Mapped[ShowroomAppointment] = relationship(back_populates="messages")
