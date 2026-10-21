import enum
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Integer, Numeric, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, IdTimestampMixin, TenantMixin


class CustomerType(str, enum.Enum):
    distributor = "distributor"
    retailer = "retailer"
    dealer = "dealer"
    other = "other"


class CustomerStatus(str, enum.Enum):
    active = "active"
    lead = "lead"          # created from an unknown WhatsApp number (FR-M2-07)
    inactive = "inactive"


class Customer(IdTimestampMixin, TenantMixin, Base):
    __tablename__ = "customers"
    __table_args__ = (UniqueConstraint("tenant_id", "whatsapp_number", name="uq_customer_whatsapp"),)

    business_name: Mapped[str] = mapped_column(String(200), index=True)
    contact_person: Mapped[str | None] = mapped_column(String(150))
    whatsapp_number: Mapped[str] = mapped_column(String(20))  # E.164, e.g. +919876543210
    alternate_phone: Mapped[str | None] = mapped_column(String(20))
    email: Mapped[str | None] = mapped_column(String(254))
    gstin: Mapped[str | None] = mapped_column(String(15))
    billing_address: Mapped[str | None] = mapped_column(String(500))
    shipping_address: Mapped[str | None] = mapped_column(String(500))
    city: Mapped[str | None] = mapped_column(String(100))
    state: Mapped[str | None] = mapped_column(String(100))
    pincode: Mapped[str | None] = mapped_column(String(6))

    customer_type: Mapped[CustomerType] = mapped_column(
        Enum(CustomerType, name="customer_type"), default=CustomerType.retailer
    )
    status: Mapped[CustomerStatus] = mapped_column(
        Enum(CustomerStatus, name="customer_status"), default=CustomerStatus.active
    )
    tags: Mapped[list[str]] = mapped_column(ARRAY(String(50)), default=list)

    price_list_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("price_lists.id", ondelete="SET NULL")
    )
    assigned_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    credit_limit: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0)
    credit_days: Mapped[int] = mapped_column(Integer, default=0)
    # amount the buyer owed at go-live; the M7 ledger starts from this
    opening_balance: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0, server_default="0")

    # WhatsApp consent (FR-M2-06, FR-M4-10)
    whatsapp_opt_in: Mapped[bool] = mapped_column(Boolean, default=False)
    opt_in_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    opt_in_source: Mapped[str | None] = mapped_column(String(50))
    opted_out: Mapped[bool] = mapped_column(Boolean, default=False)
    reminders_paused: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")  # FR-M8-10
