import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.core.validators import normalize_phone, validate_gstin, validate_pincode
from app.models import CustomerStatus, CustomerType


def _clean_tags(tags: list[str] | None) -> list[str] | None:
    if tags is None:
        return None
    seen: list[str] = []
    for t in tags:
        t = t.strip().lower()
        if t and t not in seen:
            seen.append(t)
    return seen


class CustomerFields(BaseModel):
    business_name: str | None = Field(default=None, min_length=2, max_length=200)
    contact_person: str | None = None
    whatsapp_number: str | None = None
    alternate_phone: str | None = None
    email: EmailStr | None = None
    gstin: str | None = None
    billing_address: str | None = None
    shipping_address: str | None = None
    city: str | None = None
    state: str | None = None
    pincode: str | None = None
    customer_type: CustomerType | None = None
    tags: list[str] | None = None
    price_list_id: uuid.UUID | None = None
    assigned_user_id: uuid.UUID | None = None
    credit_limit: Decimal | None = Field(default=None, ge=0)
    credit_days: int | None = Field(default=None, ge=0, le=365)
    opening_balance: Decimal | None = None
    whatsapp_opt_in: bool | None = None
    opt_in_source: str | None = None

    @field_validator("whatsapp_number", "alternate_phone")
    @classmethod
    def _phone(cls, v: str | None) -> str | None:
        return normalize_phone(v) if v else None

    @field_validator("gstin")
    @classmethod
    def _gstin(cls, v: str | None) -> str | None:
        return validate_gstin(v) if v else None

    @field_validator("pincode")
    @classmethod
    def _pincode(cls, v: str | None) -> str | None:
        return validate_pincode(v) if v else None

    @field_validator("tags")
    @classmethod
    def _tags(cls, v: list[str] | None) -> list[str] | None:
        return _clean_tags(v)


class CustomerCreate(CustomerFields):
    business_name: str = Field(min_length=2, max_length=200)
    whatsapp_number: str
    customer_type: CustomerType = CustomerType.retailer
    tags: list[str] = []
    credit_limit: Decimal = Field(default=Decimal("0"), ge=0)
    credit_days: int = Field(default=0, ge=0, le=365)
    opening_balance: Decimal = Decimal("0")
    whatsapp_opt_in: bool = False


class CustomerUpdate(CustomerFields):
    status: CustomerStatus | None = None


class CustomerOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    business_name: str
    contact_person: str | None
    whatsapp_number: str
    alternate_phone: str | None
    email: str | None
    gstin: str | None
    billing_address: str | None
    shipping_address: str | None
    city: str | None
    state: str | None
    pincode: str | None
    customer_type: CustomerType
    status: CustomerStatus
    tags: list[str]
    price_list_id: uuid.UUID | None
    assigned_user_id: uuid.UUID | None
    credit_limit: Decimal
    credit_days: int
    opening_balance: Decimal
    whatsapp_opt_in: bool
    opt_in_at: datetime | None
    opt_in_source: str | None
    opted_out: bool
    created_at: datetime
    updated_at: datetime
