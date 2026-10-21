"""M10: buyer-bot FAQ, business assistant history, owner rules and the daily summary."""

import uuid
from datetime import UTC, date, datetime

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, IdTimestampMixin, TenantMixin


def _now() -> datetime:
    return datetime.now(UTC)


class FaqEntry(IdTimestampMixin, TenantMixin, Base):
    """General questions the buyer bot answers (delivery areas, timings, returns...) - FR-M10-03."""

    __tablename__ = "faq_entries"

    question: Mapped[str] = mapped_column(String(300))
    answer: Mapped[str] = mapped_column(Text)
    answer_ta: Mapped[str | None] = mapped_column(Text)
    answer_hi: Mapped[str | None] = mapped_column(Text)
    keywords: Mapped[list[str]] = mapped_column(ARRAY(String(50)), default=list)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    times_used: Mapped[int] = mapped_column(Integer, default=0, server_default="0")


class AssistantMessage(TenantMixin, Base):
    """One question to the business assistant and its answer (FR-M10-06)."""

    __tablename__ = "assistant_messages"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, server_default=func.now(),
                                                 index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"),
                                               index=True)
    question: Mapped[str] = mapped_column(String(1000))
    kind: Mapped[str] = mapped_column(String(20))  # answer | rule_proposal | unknown | error
    tool: Mapped[str | None] = mapped_column(String(50))
    params: Mapped[dict | None] = mapped_column(JSONB)
    answer: Mapped[str] = mapped_column(Text)
    columns: Mapped[list | None] = mapped_column(JSONB)  # [{"key": "...", "label": "...", "type": "money|int|date|text"}]
    rows: Mapped[list | None] = mapped_column(JSONB)
    total_rows: Mapped[int] = mapped_column(Integer, default=0)
    rule_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("automation_rules.id", ondelete="SET NULL"))
    provider: Mapped[str | None] = mapped_column(String(20))


class AutomationRule(IdTimestampMixin, TenantMixin, Base):
    """A standing instruction from the owner, created only after they confirm it (FR-M10-09).

    kind "overdue_reminder": every <weekday> at <hour> IST, remind every buyer with an invoice more than
    <min_days> days overdue (same message and rules as M8 reminders).
    """

    __tablename__ = "automation_rules"

    kind: Mapped[str] = mapped_column(String(30), default="overdue_reminder")
    description: Mapped[str] = mapped_column(String(300))
    params: Mapped[dict] = mapped_column(JSONB, default=dict)  # {"min_days": 60}
    weekdays: Mapped[list[int]] = mapped_column(ARRAY(Integer), default=list)  # 0 = Monday ... 6 = Sunday
    hour: Mapped[int] = mapped_column(Integer, default=10)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_run_on: Mapped[date | None] = mapped_column(Date)
    last_run_summary: Mapped[str | None] = mapped_column(String(300))
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))


class DailySummary(TenantMixin, Base):
    """Morning summary for the owner (FR-M10-08): stored for the panel and sent on WhatsApp."""

    __tablename__ = "daily_summaries"
    __table_args__ = (UniqueConstraint("tenant_id", "summary_date", name="uq_daily_summary_day"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, server_default=func.now())
    summary_date: Mapped[date] = mapped_column(Date)  # the day it was sent; figures are for the day before
    data: Mapped[dict] = mapped_column(JSONB)
    text: Mapped[str] = mapped_column(Text)
    sent_to: Mapped[str | None] = mapped_column(String(20))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    send_error: Mapped[str | None] = mapped_column(String(500))
