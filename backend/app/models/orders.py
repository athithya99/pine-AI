import enum
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, IdTimestampMixin, TenantMixin


class OrderStatus(str, enum.Enum):
    draft = "draft"            # created by AI or staff, not yet sent to buyer
    quoted = "quoted"          # priced quote sent, waiting for buyer
    expired = "expired"        # quote not confirmed in time
    confirmed = "confirmed"    # buyer confirmed (or staff on buyer's instruction)
    on_hold = "on_hold"        # over credit limit / out-of-stock item; needs Owner/Admin
    approved = "approved"      # seller accepted; ready to invoice
    invoiced = "invoiced"
    dispatched = "dispatched"
    delivered = "delivered"
    cancelled = "cancelled"


class OrderSource(str, enum.Enum):
    panel = "panel"
    whatsapp = "whatsapp"      # AI capture (M5)
    excel = "excel"


class Order(IdTimestampMixin, TenantMixin, Base):
    __tablename__ = "orders"
    __table_args__ = (UniqueConstraint("tenant_id", "number", name="uq_order_number"),)

    number: Mapped[str] = mapped_column(String(40), index=True)
    customer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("customers.id", ondelete="RESTRICT"), index=True
    )
    # the customer's sales user when the order was created (for filters and "own" access)
    sales_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    source: Mapped[OrderSource] = mapped_column(Enum(OrderSource, name="order_source"), default=OrderSource.panel)
    source_message_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("messages.id", ondelete="SET NULL")
    )
    status: Mapped[OrderStatus] = mapped_column(
        Enum(OrderStatus, name="order_status"), default=OrderStatus.draft, index=True
    )
    revision: Mapped[int] = mapped_column(Integer, default=1)  # bumped on every line edit
    # lines the AI couldn't match confidently: [{"text", "quantity", "unit", "candidates": [...]}]
    ai_unclear: Mapped[list | None] = mapped_column(JSONB)
    notes: Mapped[str | None] = mapped_column(String(1000))

    gross_total: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0)
    discount_total: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0)
    taxable_total: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0)
    gst_total: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0)
    grand_total: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0)

    quoted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    quote_valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    confirmed_by_buyer: Mapped[bool] = mapped_column(Boolean, default=False)
    hold_reasons: Mapped[str | None] = mapped_column(String(500))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approved_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    approval_remark: Mapped[str | None] = mapped_column(String(500))

    # invoice link; invoice_id becomes a real FK in M7
    invoice_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    invoice_number: Mapped[str | None] = mapped_column(String(40))
    invoice_date: Mapped[date | None] = mapped_column(Date)
    invoiced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    vehicle_number: Mapped[str | None] = mapped_column(String(20))
    transporter: Mapped[str | None] = mapped_column(String(100))
    lr_number: Mapped[str | None] = mapped_column(String(50))
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancel_reason: Mapped[str | None] = mapped_column(String(500))
    cancelled_by_buyer: Mapped[bool] = mapped_column(Boolean, default=False)

    lines: Mapped[list["OrderLine"]] = relationship(
        back_populates="order", cascade="all, delete-orphan", order_by="OrderLine.position"
    )


class OrderLine(IdTimestampMixin, TenantMixin, Base):
    __tablename__ = "order_lines"

    order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("orders.id", ondelete="CASCADE"), index=True
    )
    position: Mapped[int] = mapped_column(Integer, default=1)
    product_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("products.id", ondelete="RESTRICT"))
    # snapshot so later catalog edits don't change past orders
    sku: Mapped[str] = mapped_column(String(50))
    name: Mapped[str] = mapped_column(String(200))
    uom: Mapped[str] = mapped_column(String(20), default="PCS")
    hsn_code: Mapped[str | None] = mapped_column(String(8))

    quantity: Mapped[Decimal] = mapped_column(Numeric(12, 3))
    free_qty: Mapped[Decimal] = mapped_column(Numeric(12, 3), default=0)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    price_source: Mapped[str] = mapped_column(String(20))
    gross: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    discount: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0)
    scheme_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    scheme_name: Mapped[str | None] = mapped_column(String(150))
    taxable_value: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    gst_rate: Mapped[Decimal] = mapped_column(Numeric(5, 2))
    gst_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    line_total: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    out_of_stock: Mapped[bool] = mapped_column(Boolean, default=False)

    # filled by AI capture (M5)
    match_confidence: Mapped[Decimal | None] = mapped_column(Numeric(4, 3))
    raw_text: Mapped[str | None] = mapped_column(String(300))

    # set when invoiced; ordered - invoiced = short supply (FR-M6-09)
    invoiced_qty: Mapped[Decimal | None] = mapped_column(Numeric(12, 3))

    order: Mapped[Order] = relationship(back_populates="lines")


def _now() -> datetime:
    return datetime.now(UTC)


class OrderEvent(IdTimestampMixin, TenantMixin, Base):
    """Timeline of an order: status changes, edits and buyer notifications."""

    __tablename__ = "order_events"

    # per-row Python time so events written in one transaction keep their order
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, server_default=func.now())

    order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("orders.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(30))  # status, edit, note
    from_status: Mapped[str | None] = mapped_column(String(20))
    to_status: Mapped[str | None] = mapped_column(String(20))
    actor: Mapped[str] = mapped_column(String(20))  # user, buyer, system
    user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    remark: Mapped[str | None] = mapped_column(Text)
    message_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("messages.id", ondelete="SET NULL")
    )
    notify_note: Mapped[str | None] = mapped_column(String(300))  # why the buyer was not messaged


class OrderSequence(IdTimestampMixin, TenantMixin, Base):
    """Running order number per tenant per financial year (FR-M6-04)."""

    __tablename__ = "order_sequences"
    __table_args__ = (UniqueConstraint("tenant_id", "fy", name="uq_order_seq_fy"),)

    fy: Mapped[str] = mapped_column(String(5))  # "26-27"
    last_number: Mapped[int] = mapped_column(Integer, default=0)
