"""WhatsApp campaigns (SRS section 12, M9)."""

import enum
import uuid
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, Numeric, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, IdTimestampMixin, TenantMixin


def _now() -> datetime:
    return datetime.now(UTC)


class CampaignStatus(str, enum.Enum):
    draft = "draft"
    scheduled = "scheduled"
    sending = "sending"        # recipients being queued; waits when the messaging limit is reached
    completed = "completed"    # every recipient queued or skipped
    cancelled = "cancelled"


class RecipientStatus(str, enum.Enum):
    pending = "pending"        # eligible, waiting for its turn (messaging limit)
    queued = "queued"          # WhatsApp message created; delivery status lives on the message
    skipped = "skipped"        # excluded: opted out / no opt-in / campaign cancelled
    failed = "failed"          # could not be queued (e.g. template no longer approved)


class Campaign(IdTimestampMixin, TenantMixin, Base):
    __tablename__ = "campaigns"

    name: Mapped[str] = mapped_column(String(150))
    status: Mapped[CampaignStatus] = mapped_column(Enum(CampaignStatus, name="campaign_status"),
                                                   default=CampaignStatus.draft, index=True)
    template_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("message_templates.id", ondelete="SET NULL"))
    # one entry per {{n}}: {"kind": "fixed" | "field", "value": "..."}
    variables: Mapped[list] = mapped_column(JSONB, default=list)
    # segment filters (FR-M9-01), see services/campaigns.py SEGMENT_KEYS
    segment: Mapped[dict] = mapped_column(JSONB, default=dict)
    media_path: Mapped[str | None] = mapped_column(String(500))
    media_mime: Mapped[str | None] = mapped_column(String(100))
    media_filename: Mapped[str | None] = mapped_column(String(255))
    wa_media_id: Mapped[str | None] = mapped_column(String(128))  # uploaded once, reused for every recipient
    # index of the template quick-reply button that means "Order now" (FR-M9-07); None = no order button
    order_button_index: Mapped[int | None] = mapped_column(Integer)
    product_ids: Mapped[list[uuid.UUID]] = mapped_column(ARRAY(UUID(as_uuid=True)), default=list)

    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    message_rate: Mapped[Decimal | None] = mapped_column(Numeric(6, 4))  # rate used for the estimate at send time
    waiting_note: Mapped[str | None] = mapped_column(String(300))       # e.g. "messaging limit reached"
    last_test_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_test_to: Mapped[str | None] = mapped_column(String(20))

    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    sent_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))


class CampaignRecipient(TenantMixin, Base):
    __tablename__ = "campaign_recipients"
    __table_args__ = (UniqueConstraint("campaign_id", "customer_id", name="uq_campaign_recipient"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, server_default=func.now())
    campaign_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("campaigns.id", ondelete="CASCADE"), index=True)
    customer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("customers.id", ondelete="CASCADE"), index=True)
    status: Mapped[RecipientStatus] = mapped_column(Enum(RecipientStatus, name="campaign_recipient_status"),
                                                    default=RecipientStatus.pending, index=True)
    skip_reason: Mapped[str | None] = mapped_column(String(300))
    message_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("messages.id", ondelete="SET NULL"), index=True)
    queued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    order_tapped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
