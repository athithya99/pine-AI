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
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, IdTimestampMixin, TenantMixin


def _now() -> datetime:
    return datetime.now(UTC)


class InvoiceSource(str, enum.Enum):
    generated = "generated"  # made here from an approved order (PDF generated)
    erp = "erp"              # raised in the seller's ERP; number entered against an order
    uploaded = "uploaded"    # PDF uploaded and read by AI
    excel = "excel"          # imported from Excel


class InvoiceStatus(str, enum.Enum):
    review = "review"        # uploaded, AI-read, waiting for staff to confirm (not shared, not in ledger)
    issued = "issued"
    cancelled = "cancelled"


class Invoice(IdTimestampMixin, TenantMixin, Base):
    __tablename__ = "invoices"
    __table_args__ = (UniqueConstraint("tenant_id", "number", name="uq_invoice_number"),)

    number: Mapped[str | None] = mapped_column(String(40), index=True)  # None only while in review
    invoice_date: Mapped[date | None] = mapped_column(Date, index=True)
    due_date: Mapped[date | None] = mapped_column(Date, index=True)
    customer_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("customers.id", ondelete="RESTRICT"), index=True)
    order_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("orders.id", ondelete="SET NULL"), index=True)
    source: Mapped[InvoiceSource] = mapped_column(Enum(InvoiceSource, name="invoice_source"))
    status: Mapped[InvoiceStatus] = mapped_column(Enum(InvoiceStatus, name="invoice_status"), index=True)

    seller_gstin: Mapped[str | None] = mapped_column(String(15))
    buyer_gstin: Mapped[str | None] = mapped_column(String(15))
    place_of_supply: Mapped[str | None] = mapped_column(String(100))
    inter_state: Mapped[bool] = mapped_column(Boolean, default=False)

    taxable_value: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0)
    cgst: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0)
    sgst: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0)
    igst: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0)
    tax_total: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0)
    round_off: Mapped[Decimal] = mapped_column(Numeric(6, 2), default=0)
    total: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0)
    amount_paid: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0)  # maintained by M8 allocations
    reminders_paused: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")  # e.g. disputed

    pdf_path: Mapped[str | None] = mapped_column(String(300))
    pdf_name: Mapped[str | None] = mapped_column(String(200))
    ai_extracted: Mapped[dict | None] = mapped_column(JSONB)  # what the AI read from an uploaded PDF
    notes: Mapped[str | None] = mapped_column(String(1000))

    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    confirmed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sent_message_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("messages.id", ondelete="SET NULL"))
    send_note: Mapped[str | None] = mapped_column(String(300))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancel_reason: Mapped[str | None] = mapped_column(String(500))

    lines: Mapped[list["InvoiceLine"]] = relationship(
        back_populates="invoice", cascade="all, delete-orphan", order_by="InvoiceLine.position")


class InvoiceLine(IdTimestampMixin, TenantMixin, Base):
    __tablename__ = "invoice_lines"

    invoice_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("invoices.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer, default=1)
    product_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("products.id", ondelete="SET NULL"))
    description: Mapped[str] = mapped_column(String(200))
    sku: Mapped[str | None] = mapped_column(String(50))
    hsn_code: Mapped[str | None] = mapped_column(String(8))
    uom: Mapped[str] = mapped_column(String(20), default="PCS")
    quantity: Mapped[Decimal] = mapped_column(Numeric(12, 3))
    free_qty: Mapped[Decimal] = mapped_column(Numeric(12, 3), default=0)
    rate: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    discount: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0)
    taxable_value: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    gst_rate: Mapped[Decimal] = mapped_column(Numeric(5, 2))
    cgst: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0)
    sgst: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0)
    igst: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0)
    total: Mapped[Decimal] = mapped_column(Numeric(14, 2))

    invoice: Mapped[Invoice] = relationship(back_populates="lines")


class CreditNoteStatus(str, enum.Enum):
    issued = "issued"
    cancelled = "cancelled"


class CreditNote(IdTimestampMixin, TenantMixin, Base):
    __tablename__ = "credit_notes"
    __table_args__ = (UniqueConstraint("tenant_id", "number", name="uq_credit_note_number"),)

    number: Mapped[str] = mapped_column(String(40))
    note_date: Mapped[date] = mapped_column(Date)
    customer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("customers.id", ondelete="RESTRICT"), index=True)
    invoice_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("invoices.id", ondelete="SET NULL"))
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    reason: Mapped[str] = mapped_column(String(500))
    status: Mapped[CreditNoteStatus] = mapped_column(Enum(CreditNoteStatus, name="credit_note_status"))
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancel_reason: Mapped[str | None] = mapped_column(String(500))


class LedgerKind(str, enum.Enum):
    invoice = "invoice"
    invoice_cancelled = "invoice_cancelled"
    credit_note = "credit_note"
    credit_note_cancelled = "credit_note_cancelled"
    payment = "payment"                    # M8
    payment_reversed = "payment_reversed"  # M8 (bounced cheque etc.)


class LedgerEntry(TenantMixin, Base):
    """Customer ledger (FR-M7-07). Debit = buyer owes more; credit = buyer owes less.

    Rows are never edited or deleted: a cancellation adds a reversing row. The opening balance lives on the
    customer; balance at any date = opening balance + sum(debit - credit) up to that date.
    """

    __tablename__ = "ledger_entries"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, server_default=func.now())
    customer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("customers.id", ondelete="CASCADE"), index=True)
    entry_date: Mapped[date] = mapped_column(Date, index=True)
    kind: Mapped[LedgerKind] = mapped_column(Enum(LedgerKind, name="ledger_kind"))
    ref_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    ref_number: Mapped[str | None] = mapped_column(String(60))
    debit: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0)
    credit: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0)
    narration: Mapped[str | None] = mapped_column(String(300))


class DocSequence(IdTimestampMixin, TenantMixin, Base):
    """Running numbers per tenant, document kind and financial year (FR-M7-04)."""

    __tablename__ = "doc_sequences"
    __table_args__ = (UniqueConstraint("tenant_id", "kind", "fy", name="uq_doc_seq"),)

    kind: Mapped[str] = mapped_column(String(20))  # invoice | credit_note
    fy: Mapped[str] = mapped_column(String(5))
    last_number: Mapped[int] = mapped_column(Integer, default=0)
