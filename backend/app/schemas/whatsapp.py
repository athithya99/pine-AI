import re
import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models import (
    Direction,
    MessageStatus,
    MessageType,
    SenderKind,
    TemplateCategory,
    TemplateStatus,
    WaMode,
)


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ---------- account ----------
class AccountIn(BaseModel):
    mode: WaMode
    waba_id: str | None = Field(default=None, pattern=r"^\d{5,32}$")
    phone_number_id: str | None = Field(default=None, pattern=r"^\d{5,32}$")
    display_phone_number: str | None = None
    access_token: str | None = Field(default=None, description="Leave empty to keep the saved token")
    app_secret: str | None = Field(default=None, description="Leave empty to keep the saved secret")

    @model_validator(mode="after")
    def _cloud_needs_ids(self):
        if self.mode == WaMode.cloud and not (self.waba_id and self.phone_number_id):
            raise ValueError("WhatsApp Business Account ID and Phone number ID are required for a live number")
        return self


class AccountOut(BaseModel):
    connected: bool
    mode: WaMode | None = None
    waba_id: str | None = None
    phone_number_id: str | None = None
    display_phone_number: str | None = None
    access_token_masked: str | None = None
    app_secret_masked: str | None = None
    webhook_url_path: str = "/api/webhooks/whatsapp"
    verify_token_hint: str | None = None
    simulator_enabled: bool = False


# ---------- templates ----------
TEMPLATE_NAME = re.compile(r"^[a-z0-9_]{1,512}$")


class TemplateButton(BaseModel):
    type: Literal["QUICK_REPLY"] = "QUICK_REPLY"
    text: str = Field(min_length=1, max_length=25)


class TemplateIn(BaseModel):
    name: str
    language: str = Field(default="en", max_length=10)
    category: TemplateCategory
    header_format: Literal["TEXT", "IMAGE", "DOCUMENT"] | None = None
    header_text: str | None = Field(default=None, max_length=60)
    body: str = Field(min_length=1, max_length=1024)
    footer: str | None = Field(default=None, max_length=60)
    buttons: list[TemplateButton] = Field(default_factory=list, max_length=3)
    example_values: list[str] = []

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        v = v.strip().lower().replace(" ", "_")
        if not TEMPLATE_NAME.match(v):
            raise ValueError("Use lowercase letters, numbers and underscores only")
        return v

    @model_validator(mode="after")
    def _vars(self):
        nums = sorted({int(n) for n in re.findall(r"\{\{(\d+)\}\}", self.body)})
        if nums and nums != list(range(1, len(nums) + 1)):
            raise ValueError("Variables must be numbered {{1}}, {{2}}, ... without gaps")
        if self.example_values and len(self.example_values) != len(nums):
            raise ValueError(f"Give one example value for each of the {len(nums)} variable(s)")
        if self.header_format in ("IMAGE", "DOCUMENT"):
            self.header_text = None
        elif self.header_text:
            self.header_format = "TEXT"
        else:
            self.header_format = None
        return self


class TemplateOut(ORM):
    id: uuid.UUID
    name: str
    language: str
    category: TemplateCategory
    header_format: str | None = None
    header_text: str | None
    body: str
    footer: str | None
    buttons: list | None
    variable_count: int
    status: TemplateStatus
    rejection_reason: str | None
    created_at: datetime


# ---------- inbox ----------
class ConversationOut(BaseModel):
    id: uuid.UUID
    customer_id: uuid.UUID
    customer_name: str
    whatsapp_number: str
    customer_status: str
    assigned_user_id: uuid.UUID | None
    opted_out: bool
    whatsapp_opt_in: bool
    bot_enabled: bool
    unread_count: int
    last_message_at: datetime | None
    last_message_preview: str | None
    window_open: bool
    window_closes_at: datetime | None
    handover_at: datetime | None = None
    handover_reason: str | None = None


class MessageOut(ORM):
    id: uuid.UUID
    direction: Direction
    msg_type: MessageType
    text: str | None
    payload: dict | None
    has_media: bool = False
    media_mime: str | None
    media_filename: str | None
    status: MessageStatus
    error_code: str | None
    error_message: str | None
    sender: SenderKind
    sent_by_user_id: uuid.UUID | None
    created_at: datetime


class ButtonIn(BaseModel):
    id: str = Field(min_length=1, max_length=256)
    title: str = Field(min_length=1, max_length=20)


class ListRowIn(BaseModel):
    id: str = Field(min_length=1, max_length=200)
    title: str = Field(min_length=1, max_length=24)
    description: str | None = Field(default=None, max_length=72)


class SendIn(BaseModel):
    """Exactly one of text / buttons / list / template."""

    kind: Literal["text", "buttons", "list", "template"] = "text"
    text: str | None = Field(default=None, max_length=4096)
    buttons: list[ButtonIn] | None = None
    list_button: str | None = Field(default=None, max_length=20)
    list_rows: list[ListRowIn] | None = None
    template_id: uuid.UUID | None = None
    template_params: list[str] = []

    @model_validator(mode="after")
    def _check(self):
        if self.kind in ("text", "buttons", "list") and not (self.text and self.text.strip()):
            raise ValueError("Message text is required")
        if self.kind == "buttons" and not self.buttons:
            raise ValueError("Add at least one button")
        if self.kind == "list" and not (self.list_rows and self.list_button):
            raise ValueError("A list needs a button label and rows")
        if self.kind == "template" and not self.template_id:
            raise ValueError("Pick a template")
        return self


class ConversationPatch(BaseModel):
    bot_enabled: bool
    resolved: bool = False  # clear the "needs a person" flag without switching the bot back on


# ---------- simulator ----------
class SimInbound(BaseModel):
    from_number: str
    profile_name: str | None = None
    text: str | None = None
    reply_id: str | None = None      # tap a button / list row we sent
    reply_title: str | None = None


class SimStatus(BaseModel):
    message_id: uuid.UUID
    status: Literal["delivered", "read", "failed"]
    error: str | None = None
