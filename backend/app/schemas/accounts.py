import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator

from app.core.validators import normalize_phone, validate_gstin
from app.models import Role, TenantStatus


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ---------- auth ----------
class PasswordLogin(BaseModel):
    email: EmailStr
    password: str


class OtpRequest(BaseModel):
    mobile: str

    @field_validator("mobile")
    @classmethod
    def _mobile(cls, v: str) -> str:
        return normalize_phone(v)


class OtpVerify(OtpRequest):
    code: str = Field(min_length=4, max_length=8)


class RefreshRequest(BaseModel):
    refresh_token: str


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class PasswordResetRequest(BaseModel):
    email: EmailStr


class PasswordResetConfirm(BaseModel):
    token: str
    new_password: str = Field(min_length=8)


# ---------- tenants ----------
class TenantBase(BaseModel):
    name: str = Field(min_length=2, max_length=200)
    logo_url: str | None = None
    gstin: str | None = None
    billing_address: str | None = None
    state: str | None = None
    contact_email: EmailStr | None = None
    contact_phone: str | None = None

    @field_validator("gstin")
    @classmethod
    def _gstin(cls, v: str | None) -> str | None:
        return validate_gstin(v) if v else None

    @field_validator("contact_phone")
    @classmethod
    def _phone(cls, v: str | None) -> str | None:
        return normalize_phone(v) if v else None


class TenantCreate(TenantBase):
    """Super admin creates a company together with its owner account."""

    owner_name: str
    owner_email: EmailStr
    owner_mobile: str | None = None
    owner_password: str = Field(min_length=8)

    @field_validator("owner_mobile")
    @classmethod
    def _owner_mobile(cls, v: str | None) -> str | None:
        return normalize_phone(v) if v else None


class TenantUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=200)
    logo_url: str | None = None
    gstin: str | None = None
    billing_address: str | None = None
    state: str | None = None
    contact_email: EmailStr | None = None
    contact_phone: str | None = None
    timezone: str | None = None
    fy_start_month: int | None = Field(default=None, ge=1, le=12)
    order_prefix: str | None = Field(default=None, pattern=r"^[A-Za-z0-9-]{1,10}$")
    quote_validity_hours: int | None = Field(default=None, ge=1, le=168)
    ai_ordering_enabled: bool | None = None
    ai_match_threshold: Decimal | None = Field(default=None, ge=Decimal("0.50"), le=Decimal("0.99"))
    # invoice numbers are at most 16 characters (GST rule): PREFIX/26-27/0001
    invoice_prefix: str | None = Field(default=None, pattern=r"^[A-Za-z0-9-]{1,5}$")
    credit_note_prefix: str | None = Field(default=None, pattern=r"^[A-Za-z0-9-]{1,5}$")
    invoice_auto_send: bool | None = None
    reminders_enabled: bool | None = None
    reminder_offsets: list[int] | None = Field(default=None, max_length=8)
    receipt_prefix: str | None = Field(default=None, pattern=r"^[A-Za-z0-9-]{1,5}$")
    campaign_message_rate: Decimal | None = Field(default=None, ge=0, le=100)
    wa_daily_limit: int | None = Field(default=None, ge=1, le=10_000_000)
    daily_summary_enabled: bool | None = None
    daily_summary_time: str | None = Field(default=None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    daily_summary_to: str | None = None

    @field_validator("reminder_offsets")
    @classmethod
    def _offsets(cls, v: list[int] | None) -> list[int] | None:
        if v is None:
            return None
        if any(d < -30 or d > 180 for d in v):
            raise ValueError("Reminder days must be between 30 days before and 180 days after the due date")
        return sorted(set(v))

    @field_validator("invoice_prefix", "credit_note_prefix", "receipt_prefix")
    @classmethod
    def _doc_prefix(cls, v: str | None) -> str | None:
        return v.upper() if v else None

    @field_validator("order_prefix")
    @classmethod
    def _prefix(cls, v: str | None) -> str | None:
        return v.upper() if v else None

    @field_validator("gstin")
    @classmethod
    def _gstin(cls, v: str | None) -> str | None:
        return validate_gstin(v) if v else None

    @field_validator("contact_phone", "daily_summary_to")
    @classmethod
    def _phone(cls, v: str | None) -> str | None:
        return normalize_phone(v) if v else None


class TenantOut(ORM):
    id: uuid.UUID
    name: str
    logo_url: str | None
    gstin: str | None
    billing_address: str | None
    state: str | None
    contact_email: str | None
    contact_phone: str | None
    status: TenantStatus
    currency: str
    timezone: str
    fy_start_month: int
    order_prefix: str
    quote_validity_hours: int
    ai_ordering_enabled: bool
    ai_match_threshold: Decimal
    invoice_prefix: str
    credit_note_prefix: str
    invoice_auto_send: bool
    reminders_enabled: bool
    reminder_offsets: list[int]
    receipt_prefix: str
    campaign_message_rate: Decimal
    wa_daily_limit: int
    daily_summary_enabled: bool
    daily_summary_time: str
    daily_summary_to: str | None
    created_at: datetime


# ---------- users ----------
COMPANY_ROLES = {Role.owner, Role.admin, Role.sales, Role.accounts}


class UserCreate(BaseModel):
    name: str = Field(min_length=2, max_length=150)
    email: EmailStr | None = None
    mobile: str | None = None
    password: str | None = Field(default=None, min_length=8)
    role: Role

    @field_validator("mobile")
    @classmethod
    def _mobile(cls, v: str | None) -> str | None:
        return normalize_phone(v) if v else None

    @field_validator("role")
    @classmethod
    def _role(cls, v: Role) -> Role:
        if v not in COMPANY_ROLES:
            raise ValueError("Role must be owner, admin, sales or accounts")
        return v

    @model_validator(mode="after")
    def _login_method(self):
        if not self.email and not self.mobile:
            raise ValueError("Provide an email or a mobile number")
        if self.email and not self.mobile and not self.password:
            raise ValueError("A password is required when the user has no mobile number")
        return self


class UserUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=150)
    email: EmailStr | None = None
    mobile: str | None = None
    role: Role | None = None
    is_active: bool | None = None

    @field_validator("mobile")
    @classmethod
    def _mobile(cls, v: str | None) -> str | None:
        return normalize_phone(v) if v else None

    @field_validator("role")
    @classmethod
    def _role(cls, v: Role | None) -> Role | None:
        if v is not None and v not in COMPANY_ROLES:
            raise ValueError("Role must be owner, admin, sales or accounts")
        return v


class UserOut(ORM):
    id: uuid.UUID
    tenant_id: uuid.UUID | None
    name: str
    email: str | None
    mobile: str | None
    role: Role
    is_active: bool
    last_login_at: datetime | None
    created_at: datetime


class MeOut(UserOut):
    tenant: TenantOut | None = None


class AuditLogOut(ORM):
    id: uuid.UUID
    user_id: uuid.UUID | None
    entity: str
    entity_id: str
    action: str
    old_values: dict | None
    new_values: dict | None
    created_at: datetime
