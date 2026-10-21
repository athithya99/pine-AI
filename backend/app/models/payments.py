import enum
import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Boolean, Date, DateTime, Enum, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, IdTimestampMixin, TenantMixin


class GatewayMode(str, enum.Enum):
    simulator = "simulator"  # no gateway account needed; links are paid from the panel
    razorpay = "razorpay"


class PaymentGatewayAccount(IdTimestampMixin, TenantMixin, Base):
    """The seller's own gateway account: money settles to the seller, never to Pinesphere (FR-M8-03)."""

    __tablename__ = "payment_gateway_accounts"
    __table_args__ = (UniqueConstraint("tenant_id", name="uq_gateway_tenant"),)

    mode: Mapped[GatewayMode] = mapped_column(Enum(GatewayMode, name="gateway_mode"), default=GatewayMode.simulator)
    key_id: Mapped[str | None] = mapped_column(String(64))
    key_secret_enc: Mapped[str | None] = mapped_column(Text)
    webhook_secret_enc: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class PaymentMode(str, enum.Enum):
    link = "link"        # paid through a payment link (gateway)
    cash = "cash"
    cheque = "cheque"
    neft = "neft"
    rtgs = "rtgs"
    upi = "upi"          # UPI outside the link
    other = "other"


class PaymentStatus(str, enum.Enum):
    proposed = "proposed"                    # read by AI from a buyer's screenshot; Accounts must confirm
    pending_clearance = "pending_clearance"  # cheque received, not cleared yet
    confirmed = "confirmed"                  # money received; in the ledger and allocated
    bounced = "bounced"                      # cheque bounced (ledger reversed if it was cleared)
    rejected = "rejected"                    # proposal rejected by staff


class PaymentSource(str, enum.Enum):
    gateway = "gateway"
    manual = "manual"
    ai_proof = "ai_proof"


class Payment(IdTimestampMixin, TenantMixin, Base):
    __tablename__ = "payments"

    number: Mapped[str] = mapped_column(String(40), index=True)  # RCPT/26-27/0001
    customer_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("customers.id", ondelete="RESTRICT"), index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    payment_date: Mapped[date] = mapped_column(Date, index=True)
    mode: Mapped[PaymentMode] = mapped_column(Enum(PaymentMode, name="payment_mode"))
    status: Mapped[PaymentStatus] = mapped_column(Enum(PaymentStatus, name="payment_status"), index=True)
    source: Mapped[PaymentSource] = mapped_column(Enum(PaymentSource, name="payment_source"))
    # UTR / cheque number / gateway payment id; unique per tenant among live payments (FR-M8-08)
    reference: Mapped[str | None] = mapped_column(String(60), index=True)
    cheque_bank: Mapped[str | None] = mapped_column(String(100))
    cheque_date: Mapped[date | None] = mapped_column(Date)
    payer_name: Mapped[str | None] = mapped_column(String(150))
    notes: Mapped[str | None] = mapped_column(String(500))
    unallocated: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0)  # on-account / advance

    link_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("payment_links.id",
                                                                                         ondelete="SET NULL"))
    gateway_payment_id: Mapped[str | None] = mapped_column(String(64))
    message_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("messages.id", ondelete="SET NULL"))  # buyer's screenshot
    ai_extracted: Mapped[dict | None] = mapped_column(JSONB)
    receipt_message_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("messages.id", ondelete="SET NULL"))
    receipt_note: Mapped[str | None] = mapped_column(String(300))

    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    confirmed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status_reason: Mapped[str | None] = mapped_column(String(300))

    allocations: Mapped[list["PaymentAllocation"]] = relationship(back_populates="payment",
                                                                 cascade="all, delete-orphan")


class PaymentAllocation(IdTimestampMixin, TenantMixin, Base):
    __tablename__ = "payment_allocations"

    payment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("payments.id", ondelete="CASCADE"), index=True)
    invoice_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("invoices.id", ondelete="CASCADE"), index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))

    payment: Mapped[Payment] = relationship(back_populates="allocations")


class LinkStatus(str, enum.Enum):
    created = "created"
    paid = "paid"
    expired = "expired"
    cancelled = "cancelled"


class PaymentLink(IdTimestampMixin, TenantMixin, Base):
    __tablename__ = "payment_links"

    customer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("customers.id", ondelete="CASCADE"), index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    invoice_ids: Mapped[list | None] = mapped_column(JSONB)  # specific invoices; None = oldest first
    gateway: Mapped[GatewayMode] = mapped_column(Enum(GatewayMode, name="gateway_mode"))
    gateway_link_id: Mapped[str | None] = mapped_column(String(64), index=True)
    short_url: Mapped[str | None] = mapped_column(String(300))
    status: Mapped[LinkStatus] = mapped_column(Enum(LinkStatus, name="link_status"), default=LinkStatus.created)
    purpose: Mapped[str] = mapped_column(String(20), default="manual")  # manual | reminder
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))


class ReminderLog(IdTimestampMixin, TenantMixin, Base):
    """One row per invoice per reminder step, so each step is sent exactly once (FR-M8-01)."""

    __tablename__ = "reminder_logs"
    __table_args__ = (UniqueConstraint("invoice_id", "offset_days", name="uq_reminder_step"),)

    invoice_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("invoices.id", ondelete="CASCADE"), index=True)
    customer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("customers.id", ondelete="CASCADE"), index=True)
    offset_days: Mapped[int] = mapped_column(Integer)  # -3 = 3 days before due, 0 = due today, 7 = 7 days after
    run_date: Mapped[date] = mapped_column(Date)
    message_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("messages.id", ondelete="SET NULL"))
    link_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("payment_links.id", ondelete="SET NULL"))
    note: Mapped[str | None] = mapped_column(String(300))  # why not sent, if not
