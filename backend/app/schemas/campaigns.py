import uuid
from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.models import CampaignStatus, CustomerType, RecipientStatus

# customer fields a template variable can be filled from
FIELD_LABELS = {
    "first_name": "First name",
    "contact_name": "Contact person",
    "business_name": "Business name",
    "city": "City",
    "state": "State",
    "outstanding": "Outstanding amount",
    "sales_person": "Sales person",
}


class Segment(BaseModel):
    """FR-M9-01. Empty lists mean "any". Only active customers are ever included."""

    customer_types: list[CustomerType] = []
    tags: list[str] = []              # customer has ANY of these tags
    cities: list[str] = []
    states: list[str] = []
    assigned_user_ids: list[uuid.UUID] = []
    no_order_days: int | None = Field(default=None, ge=1, le=3650)        # no order in the last N days
    ordered_within_days: int | None = Field(default=None, ge=1, le=3650)  # ordered in the last N days
    min_outstanding: Decimal | None = None
    max_outstanding: Decimal | None = None

    @field_validator("tags", "cities", "states")
    @classmethod
    def _clean(cls, v: list[str]) -> list[str]:
        return sorted({x.strip() for x in v if x and x.strip()})


class Variable(BaseModel):
    kind: Literal["fixed", "field"] = "fixed"
    value: str = Field(default="", max_length=500)

    @field_validator("value")
    @classmethod
    def _strip(cls, v: str) -> str:
        return v.strip()


class CampaignIn(BaseModel):
    name: str = Field(min_length=2, max_length=150)
    template_id: uuid.UUID | None = None
    variables: list[Variable] = []
    segment: Segment = Segment()
    order_button_index: int | None = Field(default=None, ge=0, le=2)
    product_ids: list[uuid.UUID] = Field(default_factory=list, max_length=10)


class SegmentPreview(BaseModel):
    matched: int
    eligible: int
    opted_out: int
    no_opt_in: int
    rate: Decimal
    estimated_cost: Decimal
    sample: list[str]
    capacity_now: int
    daily_limit: int


class ScheduleIn(BaseModel):
    scheduled_at: datetime


class SendIn(BaseModel):
    """The panel shows the count and estimated charge; the count the user confirmed is sent back."""

    confirmed_count: int = Field(ge=0)


class TestIn(BaseModel):
    whatsapp_number: str = Field(min_length=6, max_length=20)


class ProductBrief(BaseModel):
    id: uuid.UUID
    sku: str
    name: str


class CampaignOut(BaseModel):
    id: uuid.UUID
    name: str
    status: CampaignStatus
    template_id: uuid.UUID | None
    template_name: str | None
    template_body: str | None
    template_header_format: str | None
    template_buttons: list | None
    variables: list[Variable]
    segment: Segment
    has_media: bool
    media_filename: str | None
    media_mime: str | None
    order_button_index: int | None
    products: list[ProductBrief]
    scheduled_at: datetime | None
    started_at: datetime | None
    completed_at: datetime | None
    cancelled_at: datetime | None
    waiting_note: str | None
    last_test_at: datetime | None
    last_test_to: str | None
    created_by_name: str | None
    sent_by_name: str | None
    created_at: datetime
    stats: dict | None = None


class CampaignRow(BaseModel):
    id: uuid.UUID
    name: str
    status: CampaignStatus
    template_name: str | None
    scheduled_at: datetime | None
    started_at: datetime | None
    created_at: datetime
    targeted: int
    sent: int
    read: int
    orders: int
    order_value: Decimal


class RecipientRow(BaseModel):
    id: uuid.UUID
    customer_id: uuid.UUID
    customer_name: str
    whatsapp_number: str
    status: RecipientStatus
    delivery: str             # pending | queued | sent | delivered | read | failed | skipped
    reason: str | None
    queued_at: datetime | None
    replied: bool
    order_tapped: bool
    orders: int
    order_value: Decimal
