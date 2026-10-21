import enum
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Integer, Numeric, String, func
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, IdTimestampMixin


class TenantStatus(str, enum.Enum):
    active = "active"
    suspended = "suspended"


class Role(str, enum.Enum):
    super_admin = "super_admin"  # Pinesphere platform
    owner = "owner"
    admin = "admin"
    sales = "sales"
    accounts = "accounts"


class Tenant(IdTimestampMixin, Base):
    __tablename__ = "tenants"

    name: Mapped[str] = mapped_column(String(200))
    logo_url: Mapped[str | None] = mapped_column(String(500))
    gstin: Mapped[str | None] = mapped_column(String(15))
    billing_address: Mapped[str | None] = mapped_column(String(500))
    state: Mapped[str | None] = mapped_column(String(100))
    contact_email: Mapped[str | None] = mapped_column(String(254))
    contact_phone: Mapped[str | None] = mapped_column(String(20))
    status: Mapped[TenantStatus] = mapped_column(Enum(TenantStatus, name="tenant_status"), default=TenantStatus.active)
    currency: Mapped[str] = mapped_column(String(3), default="INR")
    timezone: Mapped[str] = mapped_column(String(50), default="Asia/Kolkata")
    fy_start_month: Mapped[int] = mapped_column(Integer, default=4)
    # orders (M6)
    order_prefix: Mapped[str] = mapped_column(String(10), default="ORD", server_default="ORD")
    quote_validity_hours: Mapped[int] = mapped_column(Integer, default=24, server_default="24")
    # AI order capture (M5)
    ai_ordering_enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    # invoices (M7)
    invoice_prefix: Mapped[str] = mapped_column(String(5), default="INV", server_default="INV")
    credit_note_prefix: Mapped[str] = mapped_column(String(5), default="CN", server_default="CN")
    # collections (M8): days relative to due date; negative = before
    reminders_enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    reminder_offsets: Mapped[list[int]] = mapped_column(ARRAY(Integer), default=lambda: [-3, 0, 7, 15],
                                                        server_default="{-3,0,7,15}")
    receipt_prefix: Mapped[str] = mapped_column(String(5), default="RCPT", server_default="RCPT")
    invoice_auto_send: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    ai_match_threshold: Mapped[Decimal] = mapped_column(Numeric(3, 2), default=Decimal("0.80"), server_default="0.80")
    # campaigns (M9): Meta charge per marketing message (shown before sending) and the number's messaging limit
    campaign_message_rate: Mapped[Decimal] = mapped_column(Numeric(6, 4), default=Decimal("0.8800"),
                                                           server_default="0.8800")
    wa_daily_limit: Mapped[int] = mapped_column(Integer, default=1000, server_default="1000")
    # daily summary for the owner (M10): time in IST, sent to this WhatsApp number (blank = the owner's mobile)
    daily_summary_enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    daily_summary_time: Mapped[str] = mapped_column(String(5), default="09:00", server_default="09:00")
    daily_summary_to: Mapped[str | None] = mapped_column(String(20))


class User(IdTimestampMixin, Base):
    __tablename__ = "users"

    # NULL only for platform super admins
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(150))
    email: Mapped[str | None] = mapped_column(String(254), unique=True)
    mobile: Mapped[str | None] = mapped_column(String(20), unique=True)
    password_hash: Mapped[str | None] = mapped_column(String(255))
    role: Mapped[Role] = mapped_column(Enum(Role, name="user_role"))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    # bumped on deactivate / password change so existing tokens stop working
    token_version: Mapped[int] = mapped_column(Integer, default=0)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class OtpCode(IdTimestampMixin, Base):
    __tablename__ = "otp_codes"

    mobile: Mapped[str] = mapped_column(String(20), index=True)
    code_hash: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    used: Mapped[bool] = mapped_column(Boolean, default=False)


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    entity: Mapped[str] = mapped_column(String(50), index=True)
    entity_id: Mapped[str] = mapped_column(String(64), index=True)
    action: Mapped[str] = mapped_column(String(20))  # create | update | delete
    old_values: Mapped[dict | None] = mapped_column(JSONB)
    new_values: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
