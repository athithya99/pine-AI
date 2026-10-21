import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.models import CreditNoteStatus, InvoiceSource, InvoiceStatus


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class InvoiceLineOut(ORM):
    id: uuid.UUID
    position: int
    description: str
    sku: str | None
    hsn_code: str | None
    uom: str
    quantity: Decimal
    free_qty: Decimal
    rate: Decimal
    discount: Decimal
    taxable_value: Decimal
    gst_rate: Decimal
    cgst: Decimal
    sgst: Decimal
    igst: Decimal
    total: Decimal


class InvoiceSummary(BaseModel):
    id: uuid.UUID
    number: str | None
    invoice_date: date | None
    due_date: date | None
    customer_id: uuid.UUID | None
    customer_name: str | None
    order_id: uuid.UUID | None
    order_number: str | None
    source: InvoiceSource
    status: InvoiceStatus
    total: Decimal
    amount_paid: Decimal
    balance_due: Decimal
    overdue: bool
    has_pdf: bool
    pdf_name: str | None
    sent: bool
    created_at: datetime


class InvoiceDetail(InvoiceSummary):
    seller_gstin: str | None
    buyer_gstin: str | None
    place_of_supply: str | None
    inter_state: bool
    taxable_value: Decimal
    cgst: Decimal
    sgst: Decimal
    igst: Decimal
    tax_total: Decimal
    round_off: Decimal
    notes: str | None
    ai_extracted: dict | None
    send_note: str | None
    sent_message_id: uuid.UUID | None
    cancel_reason: str | None
    cancelled_at: datetime | None
    confirmed_at: datetime | None
    confirmed_by_name: str | None
    created_by_name: str | None
    customer_whatsapp: str | None
    lines: list[InvoiceLineOut]
    credit_notes: list["CreditNoteOut"] = []
    amount_in_words: str
    reminders_paused: bool = False


class ReviewIn(BaseModel):
    """Staff-confirmed fields of an uploaded invoice."""
    invoice_number: str = Field(min_length=1, max_length=40)
    invoice_date: date
    due_date: date | None = None
    customer_id: uuid.UUID
    taxable_value: Decimal = Field(ge=0)
    tax_amount: Decimal = Field(ge=0)
    total: Decimal = Field(gt=0)
    send_to_buyer: bool = True


class CancelIn(BaseModel):
    reason: str = Field(min_length=2, max_length=500)


class SendIn(BaseModel):
    caption: str | None = Field(default=None, max_length=1000)


class CreditNoteIn(BaseModel):
    customer_id: uuid.UUID
    invoice_id: uuid.UUID | None = None
    amount: Decimal = Field(gt=0, max_digits=14, decimal_places=2)
    reason: str = Field(min_length=2, max_length=500)
    note_date: date | None = None


class CreditNoteOut(ORM):
    id: uuid.UUID
    number: str
    note_date: date
    customer_id: uuid.UUID
    customer_name: str | None = None
    invoice_id: uuid.UUID | None
    invoice_number: str | None = None
    amount: Decimal
    reason: str
    status: CreditNoteStatus
    cancel_reason: str | None
    created_at: datetime


class LedgerRow(BaseModel):
    id: uuid.UUID
    entry_date: date
    kind: str
    ref_id: uuid.UUID | None
    ref_number: str | None
    narration: str | None
    debit: Decimal
    credit: Decimal
    balance: Decimal


class Statement(BaseModel):
    customer_id: uuid.UUID
    customer_name: str
    opening_balance_master: Decimal
    date_from: date
    date_to: date
    opening: Decimal
    entries: list[LedgerRow]
    total_debit: Decimal
    total_credit: Decimal
    closing: Decimal


class AttachResult(BaseModel):
    matched: list[str]
    unmatched: list[str]


InvoiceDetail.model_rebuild()
