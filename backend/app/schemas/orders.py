import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.models import OrderSource, OrderStatus


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class OrderLineIn(BaseModel):
    product_id: uuid.UUID
    quantity: Decimal = Field(gt=0, max_digits=12, decimal_places=3)
    raw_text: str | None = Field(default=None, max_length=300)  # buyer's wording, kept through staff edits


class OrderCreate(BaseModel):
    customer_id: uuid.UUID
    lines: list[OrderLineIn] = Field(min_length=1, max_length=200)
    notes: str | None = Field(default=None, max_length=1000)
    # what to do right after creating: keep as draft, send the quote, or confirm on the buyer's instruction
    action: Literal["draft", "quote", "confirm"] = "draft"


class OrderLinesUpdate(BaseModel):
    lines: list[OrderLineIn] = Field(min_length=1, max_length=200)
    notes: str | None = Field(default=None, max_length=1000)


class RemarkIn(BaseModel):
    remark: str | None = Field(default=None, max_length=500)


class CancelIn(BaseModel):
    reason: str = Field(min_length=2, max_length=500)


class InvoiceLineIn(BaseModel):
    line_id: uuid.UUID
    invoiced_qty: Decimal = Field(ge=0)


class InvoiceIn(BaseModel):
    invoice_number: str = Field(min_length=1, max_length=40)
    invoice_date: date | None = None
    lines: list[InvoiceLineIn] = []


class DispatchIn(BaseModel):
    vehicle_number: str | None = Field(default=None, max_length=20)
    transporter: str | None = Field(default=None, max_length=100)
    lr_number: str | None = Field(default=None, max_length=50)


class OrderLineOut(ORM):
    id: uuid.UUID
    position: int
    product_id: uuid.UUID
    sku: str
    name: str
    uom: str
    hsn_code: str | None
    quantity: Decimal
    free_qty: Decimal
    unit_price: Decimal
    price_source: str
    gross: Decimal
    discount: Decimal
    scheme_name: str | None
    taxable_value: Decimal
    gst_rate: Decimal
    gst_amount: Decimal
    line_total: Decimal
    out_of_stock: bool
    match_confidence: Decimal | None
    raw_text: str | None
    invoiced_qty: Decimal | None


class OrderEventOut(ORM):
    id: uuid.UUID
    kind: str
    from_status: str | None
    to_status: str | None
    actor: str
    user_id: uuid.UUID | None
    user_name: str | None = None
    remark: str | None
    message_id: uuid.UUID | None
    notify_note: str | None
    created_at: datetime


class OrderSummary(ORM):
    id: uuid.UUID
    number: str
    customer_id: uuid.UUID
    customer_name: str
    sales_user_id: uuid.UUID | None
    sales_user_name: str | None
    source: OrderSource
    status: OrderStatus
    grand_total: Decimal
    line_count: int
    hold_reasons: str | None
    quote_valid_until: datetime | None
    created_at: datetime
    updated_at: datetime


class SourceMessage(BaseModel):
    id: uuid.UUID
    msg_type: str
    text: str | None
    transcript: str | None
    has_media: bool
    media_mime: str | None
    media_filename: str | None
    created_at: datetime


class CreditInfo(BaseModel):
    credit_limit: Decimal
    outstanding: Decimal
    available: Decimal | None  # None = no limit set


class OrderDetail(ORM):
    id: uuid.UUID
    number: str
    customer_id: uuid.UUID
    customer_name: str
    customer_whatsapp: str
    sales_user_id: uuid.UUID | None
    sales_user_name: str | None
    created_by_name: str | None
    source: OrderSource
    source_message_id: uuid.UUID | None
    status: OrderStatus
    revision: int
    notes: str | None
    gross_total: Decimal
    discount_total: Decimal
    taxable_total: Decimal
    gst_total: Decimal
    grand_total: Decimal
    quoted_at: datetime | None
    quote_valid_until: datetime | None
    confirmed_at: datetime | None
    confirmed_by_buyer: bool
    hold_reasons: str | None
    approved_at: datetime | None
    approval_remark: str | None
    invoice_number: str | None
    invoice_date: date | None
    vehicle_number: str | None
    transporter: str | None
    lr_number: str | None
    dispatched_at: datetime | None
    delivered_at: datetime | None
    cancelled_at: datetime | None
    cancel_reason: str | None
    cancelled_by_buyer: bool
    created_at: datetime
    updated_at: datetime
    lines: list[OrderLineOut]
    events: list[OrderEventOut]
    credit: CreditInfo
    allowed_actions: list[str]
    ai_unclear: list[dict] | None
    source_message: SourceMessage | None = None
