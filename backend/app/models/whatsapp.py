import enum
import uuid
from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, IdTimestampMixin, TenantMixin


class WaMode(str, enum.Enum):
    simulator = "simulator"  # no Meta account needed; messages stay inside the app
    cloud = "cloud"          # Meta WhatsApp Cloud API


class Direction(str, enum.Enum):
    inbound = "inbound"
    outbound = "outbound"


class MessageType(str, enum.Enum):
    text = "text"
    image = "image"
    audio = "audio"
    video = "video"
    document = "document"
    sticker = "sticker"
    location = "location"
    interactive = "interactive"  # buttons / list sent by us, or a button/list reply from buyer
    button = "button"            # quick-reply tap on a template
    template = "template"
    unsupported = "unsupported"


class MessageStatus(str, enum.Enum):
    received = "received"  # inbound
    queued = "queued"
    sent = "sent"
    delivered = "delivered"
    read = "read"
    failed = "failed"


STATUS_RANK = {
    MessageStatus.queued: 0, MessageStatus.sent: 1, MessageStatus.delivered: 2, MessageStatus.read: 3,
}


class SenderKind(str, enum.Enum):
    customer = "customer"
    user = "user"
    bot = "bot"
    system = "system"  # automated: reminders, status updates, campaigns


class TemplateCategory(str, enum.Enum):
    marketing = "MARKETING"
    utility = "UTILITY"
    authentication = "AUTHENTICATION"


class TemplateStatus(str, enum.Enum):
    draft = "DRAFT"
    pending = "PENDING"
    approved = "APPROVED"
    rejected = "REJECTED"
    paused = "PAUSED"
    disabled = "DISABLED"


class WhatsAppAccount(IdTimestampMixin, TenantMixin, Base):
    """One connected WhatsApp number per tenant in the MVP (FR-M4-01)."""

    __tablename__ = "whatsapp_accounts"
    __table_args__ = (UniqueConstraint("tenant_id", name="uq_wa_account_tenant"),)

    mode: Mapped[WaMode] = mapped_column(Enum(WaMode, name="wa_mode"), default=WaMode.simulator)
    waba_id: Mapped[str | None] = mapped_column(String(32))
    phone_number_id: Mapped[str | None] = mapped_column(String(32), unique=True)
    display_phone_number: Mapped[str | None] = mapped_column(String(20))
    access_token_enc: Mapped[str | None] = mapped_column(Text)
    app_secret_enc: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class Conversation(IdTimestampMixin, TenantMixin, Base):
    """One thread per customer (FR-M4-08)."""

    __tablename__ = "conversations"
    __table_args__ = (UniqueConstraint("tenant_id", "customer_id", name="uq_conversation_customer"),)

    customer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("customers.id", ondelete="CASCADE"), index=True
    )
    bot_enabled: Mapped[bool] = mapped_column(Boolean, default=True)  # False = human takeover (FR-M4-09)
    unread_count: Mapped[int] = mapped_column(Integer, default=0)
    last_message_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    last_inbound_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # 24h window
    last_message_preview: Mapped[str | None] = mapped_column(String(200))
    # buyer bot handed the chat to staff (FR-M10-05): asked for a person, complaint, or a question it can't answer
    handover_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    handover_reason: Mapped[str | None] = mapped_column(String(200))


def _now() -> datetime:
    return datetime.now(UTC)


class Message(IdTimestampMixin, TenantMixin, Base):
    __tablename__ = "messages"
    __table_args__ = (UniqueConstraint("tenant_id", "wa_message_id", name="uq_message_wamid"),)

    # per-row clock time (not transaction time) so messages saved together keep their order
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, server_default=func.now(), index=True)

    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    customer_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("customers.id", ondelete="CASCADE"))
    direction: Mapped[Direction] = mapped_column(Enum(Direction, name="message_direction"))
    msg_type: Mapped[MessageType] = mapped_column(Enum(MessageType, name="message_type"))
    text: Mapped[str | None] = mapped_column(Text)  # body, caption, or the tapped button title
    payload: Mapped[dict | None] = mapped_column(JSONB)  # buttons/list/template params, reply ids, raw extras
    media_path: Mapped[str | None] = mapped_column(String(500))  # our stored copy (Meta links expire)
    media_mime: Mapped[str | None] = mapped_column(String(100))
    media_filename: Mapped[str | None] = mapped_column(String(255))
    wa_message_id: Mapped[str | None] = mapped_column(String(128))
    status: Mapped[MessageStatus] = mapped_column(Enum(MessageStatus, name="message_status"))
    error_code: Mapped[str | None] = mapped_column(String(20))
    error_message: Mapped[str | None] = mapped_column(String(500))
    sender: Mapped[SenderKind] = mapped_column(Enum(SenderKind, name="sender_kind"))
    sent_by_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    wa_timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class MessageTemplate(IdTimestampMixin, TenantMixin, Base):
    """WhatsApp message template (FR-M4-05). Body uses {{1}}, {{2}} placeholders."""

    __tablename__ = "message_templates"
    __table_args__ = (UniqueConstraint("tenant_id", "name", "language", name="uq_template_name_lang"),)

    name: Mapped[str] = mapped_column(String(512))
    language: Mapped[str] = mapped_column(String(10), default="en")
    category: Mapped[TemplateCategory] = mapped_column(Enum(TemplateCategory, name="template_category"))
    header_text: Mapped[str | None] = mapped_column(String(60))
    # None/TEXT = text header (or none); IMAGE / DOCUMENT = a media header filled in at send time (M9 campaigns)
    header_format: Mapped[str | None] = mapped_column(String(10))
    body: Mapped[str] = mapped_column(Text)
    footer: Mapped[str | None] = mapped_column(String(60))
    buttons: Mapped[list | None] = mapped_column(JSONB)  # [{"type":"QUICK_REPLY","text":"Order now"}]
    example_values: Mapped[list | None] = mapped_column(JSONB)
    variable_count: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[TemplateStatus] = mapped_column(Enum(TemplateStatus, name="template_status"), default=TemplateStatus.draft)
    meta_template_id: Mapped[str | None] = mapped_column(String(64))
    rejection_reason: Mapped[str | None] = mapped_column(String(500))


class WebhookEvent(Base):
    """Raw webhook payloads, kept for debugging and re-processing."""

    __tablename__ = "webhook_events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source: Mapped[str] = mapped_column(String(20))  # whatsapp | razorpay
    payload: Mapped[dict] = mapped_column(JSONB)
    processed: Mapped[bool] = mapped_column(Boolean, default=False)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
