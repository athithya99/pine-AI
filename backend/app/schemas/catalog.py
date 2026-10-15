import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.validators import validate_hsn
from app.models import SchemeTarget, SchemeType, StockStatus


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ---------- GST rates ----------
class GstRateCreate(BaseModel):
    rate: Decimal = Field(ge=0, le=100)
    label: str | None = None


class GstRateOut(ORM):
    id: uuid.UUID
    rate: Decimal
    label: str
    is_active: bool


# ---------- products ----------
class ProductFields(BaseModel):
    sku: str | None = Field(default=None, min_length=1, max_length=50)
    name: str | None = Field(default=None, min_length=1, max_length=200)
    brand: str | None = None
    category: str | None = None
    uom: str | None = Field(default=None, max_length=20)
    pack_size: str | None = None
    hsn_code: str | None = None
    gst_rate: Decimal | None = Field(default=None, ge=0, le=100)
    mrp: Decimal | None = Field(default=None, ge=0)
    base_price: Decimal | None = Field(default=None, ge=0)
    min_order_qty: Decimal | None = Field(default=None, gt=0)
    images: list[str] | None = Field(default=None, max_length=5)
    aliases: list[str] | None = None
    stock_status: StockStatus | None = None
    is_active: bool | None = None

    @field_validator("sku")
    @classmethod
    def _sku(cls, v: str | None) -> str | None:
        return v.strip().upper() if v else v

    @field_validator("hsn_code")
    @classmethod
    def _hsn(cls, v: str | None) -> str | None:
        return validate_hsn(v) if v else None

    @field_validator("aliases")
    @classmethod
    def _aliases(cls, v: list[str] | None) -> list[str] | None:
        if v is None:
            return None
        out: list[str] = []
        for a in v:
            a = a.strip()
            if a and a.lower() not in {x.lower() for x in out}:
                out.append(a)
        return out


class ProductCreate(ProductFields):
    sku: str = Field(min_length=1, max_length=50)
    name: str = Field(min_length=1, max_length=200)
    uom: str = "PCS"
    gst_rate: Decimal = Field(ge=0, le=100)
    base_price: Decimal = Field(ge=0)
    min_order_qty: Decimal = Field(default=Decimal("1"), gt=0)
    images: list[str] = Field(default_factory=list, max_length=5)
    aliases: list[str] = []
    stock_status: StockStatus = StockStatus.in_stock


class ProductUpdate(ProductFields):
    pass


class ProductOut(ORM):
    id: uuid.UUID
    sku: str
    name: str
    brand: str | None
    category: str | None
    uom: str
    pack_size: str | None
    hsn_code: str | None
    gst_rate: Decimal
    mrp: Decimal | None
    base_price: Decimal
    min_order_qty: Decimal
    images: list[str]
    aliases: list[str]
    stock_status: StockStatus
    is_active: bool
    created_at: datetime
    updated_at: datetime


class StockUpdate(BaseModel):
    stock_status: StockStatus


# ---------- price lists ----------
class PriceListCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str | None = None


class PriceListUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = None
    is_active: bool | None = None


class PriceListOut(ORM):
    id: uuid.UUID
    name: str
    description: str | None
    is_active: bool
    created_at: datetime


class PriceEntry(BaseModel):
    product_id: uuid.UUID
    price: Decimal = Field(ge=0)


class PriceEntryOut(ORM):
    product_id: uuid.UUID
    price: Decimal


class PriceEntriesIn(BaseModel):
    items: list[PriceEntry] = Field(min_length=1)


# ---------- schemes ----------
class SchemeFields(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=150)
    scheme_type: SchemeType | None = None
    value: Decimal | None = Field(default=None, ge=0)
    min_qty: Decimal | None = Field(default=None, ge=0)
    free_qty: Decimal | None = Field(default=None, ge=0)
    start_date: date | None = None
    end_date: date | None = None
    product_ids: list[uuid.UUID] | None = None
    target: SchemeTarget | None = None
    target_values: list[str] | None = None
    is_active: bool | None = None


class SchemeCreate(SchemeFields):
    name: str = Field(min_length=1, max_length=150)
    scheme_type: SchemeType
    value: Decimal = Field(default=Decimal("0"), ge=0)
    min_qty: Decimal = Field(default=Decimal("0"), ge=0)
    free_qty: Decimal = Field(default=Decimal("0"), ge=0)
    start_date: date
    end_date: date
    product_ids: list[uuid.UUID] = Field(min_length=1)
    target: SchemeTarget = SchemeTarget.all
    target_values: list[str] = []

    @model_validator(mode="after")
    def _check(self):
        validate_scheme_values(
            self.scheme_type, self.value, self.min_qty, self.free_qty,
            self.start_date, self.end_date, self.target, self.target_values,
        )
        return self


class SchemeUpdate(SchemeFields):
    pass


def validate_scheme_values(scheme_type, value, min_qty, free_qty, start_date, end_date, target, target_values):
    if end_date < start_date:
        raise ValueError("End date must be on or after start date")
    if scheme_type == SchemeType.percent and not (0 < value <= 100):
        raise ValueError("Percentage must be between 0 and 100")
    if scheme_type == SchemeType.flat_per_unit and value <= 0:
        raise ValueError("Flat discount per unit must be greater than 0")
    if scheme_type == SchemeType.free_qty and (min_qty <= 0 or free_qty <= 0):
        raise ValueError("Free-quantity schemes need both buy quantity and free quantity")
    if target != SchemeTarget.all and not target_values:
        raise ValueError("Pick at least one tag or customer type for this target")


class SchemeOut(ORM):
    id: uuid.UUID
    name: str
    scheme_type: SchemeType
    value: Decimal
    min_qty: Decimal
    free_qty: Decimal
    start_date: date
    end_date: date
    product_ids: list[uuid.UUID]
    target: SchemeTarget
    target_values: list[str]
    is_active: bool
    created_at: datetime


# ---------- pricing ----------
class QuoteLineIn(BaseModel):
    product_id: uuid.UUID
    quantity: Decimal = Field(gt=0)


class QuoteIn(BaseModel):
    customer_id: uuid.UUID
    lines: list[QuoteLineIn] = Field(min_length=1)
    on_date: date | None = None


class QuoteLineOut(BaseModel):
    product_id: uuid.UUID
    sku: str
    name: str
    quantity: Decimal
    unit_price: Decimal
    price_source: str
    gross: Decimal
    discount: Decimal
    free_qty: Decimal
    scheme_id: uuid.UUID | None
    scheme_name: str | None
    taxable_value: Decimal
    gst_rate: Decimal
    gst_amount: Decimal
    line_total: Decimal
    stock_status: StockStatus
    below_min_qty: bool


class QuoteOut(BaseModel):
    customer_id: uuid.UUID
    lines: list[QuoteLineOut]
    taxable_total: Decimal
    gst_total: Decimal
    grand_total: Decimal
    has_out_of_stock: bool
