"""AI settings status and usage log (FR-M5-10)."""

import uuid
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.deps import ALL_STAFF, MANAGERS, require_roles
from app.models import AiLog, Customer, Message, Order, Tenant, User
from app.schemas.common import Page
from app.services.ai.providers import DEFAULT_MODELS, get_provider, stt_configured
from app.services.orders import IST

router = APIRouter(prefix="/ai", tags=["AI"])
staff = require_roles(*ALL_STAFF)
managers = require_roles(*MANAGERS)


class AiStatus(BaseModel):
    provider: str
    is_mock: bool
    configured: bool
    model_fast: str
    model_smart: str
    reads_photos: bool
    voice_notes: bool
    ai_ordering_enabled: bool
    ai_match_threshold: Decimal


class AiLogOut(BaseModel):
    id: uuid.UUID
    created_at: datetime
    kind: str
    provider: str
    model: str
    intent: str | None
    input_text: str | None
    input_media: str | None
    output: dict | None
    input_tokens: int
    output_tokens: int
    cost_usd: Decimal | None
    latency_ms: int
    error: str | None
    message_id: uuid.UUID | None
    order_id: uuid.UUID | None
    order_number: str | None = None
    customer_name: str | None = None


class AiUsage(BaseModel):
    period_from: date
    period_to: date
    calls: int
    errors: int
    input_tokens: int
    output_tokens: int
    cost_usd: Decimal
    avg_latency_ms: int
    orders_created: int
    by_intent: dict[str, int]


@router.get("/status", response_model=AiStatus)
def ai_status(db: Session = Depends(get_db), user: User = Depends(staff)):
    tenant = db.get(Tenant, user.tenant_id)
    name = (settings.AI_PROVIDER or "mock").lower()
    try:
        p = get_provider()
        fast, smart, vision = p.fast, p.smart, p.vision
    except Exception:  # unknown provider name
        fast, smart, vision = DEFAULT_MODELS.get(name, ("-", "-"))[0], "-", False
    return AiStatus(
        provider=name, is_mock=name == "mock", configured=name == "mock" or bool(settings.AI_API_KEY),
        model_fast=fast, model_smart=smart, reads_photos=vision, voice_notes=stt_configured(),
        ai_ordering_enabled=tenant.ai_ordering_enabled, ai_match_threshold=tenant.ai_match_threshold,
    )


def _range(date_from: date | None, date_to: date | None) -> tuple[date, date]:
    today = datetime.now(IST).date()
    return date_from or today.replace(day=1), date_to or today


@router.get("/usage", response_model=AiUsage)
def ai_usage(date_from: date | None = None, date_to: date | None = None, db: Session = Depends(get_db),
             user: User = Depends(managers)):
    d1, d2 = _range(date_from, date_to)
    start = datetime.combine(d1, time.min, IST)
    end = datetime.combine(d2 + timedelta(days=1), time.min, IST)
    base = select(AiLog).where(AiLog.tenant_id == user.tenant_id, AiLog.created_at >= start, AiLog.created_at < end)
    sub = base.subquery()
    row = db.execute(select(
        func.count(), func.count(sub.c.error), func.coalesce(func.sum(sub.c.input_tokens), 0),
        func.coalesce(func.sum(sub.c.output_tokens), 0), func.coalesce(func.sum(sub.c.cost_usd), 0),
        func.coalesce(func.avg(sub.c.latency_ms), 0), func.count(sub.c.order_id),
    )).one()
    intents = dict(db.execute(select(sub.c.intent, func.count()).where(sub.c.intent.is_not(None))
                              .group_by(sub.c.intent)).all())
    return AiUsage(period_from=d1, period_to=d2, calls=row[0], errors=row[1], input_tokens=row[2],
                   output_tokens=row[3], cost_usd=Decimal(row[4]).quantize(Decimal("0.0001")),
                   avg_latency_ms=int(row[5]), orders_created=row[6], by_intent=intents)


@router.get("/logs", response_model=Page[AiLogOut])
def ai_logs(
    date_from: date | None = None, date_to: date | None = None, errors_only: bool = False,
    page: int = Query(default=1, ge=1), size: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db), user: User = Depends(managers),
):
    d1, d2 = _range(date_from, date_to)
    stmt = select(AiLog).where(
        AiLog.tenant_id == user.tenant_id,
        AiLog.created_at >= datetime.combine(d1, time.min, IST),
        AiLog.created_at < datetime.combine(d2 + timedelta(days=1), time.min, IST))
    if errors_only:
        stmt = stmt.where(AiLog.error.is_not(None))
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = db.scalars(stmt.order_by(AiLog.created_at.desc()).offset((page - 1) * size).limit(size)).all()
    orders = {o.id: o.number for o in db.scalars(select(Order).where(Order.id.in_({r.order_id for r in rows if r.order_id})))}
    msg_customer = dict(db.execute(
        select(Message.id, Customer.business_name).join(Customer, Customer.id == Message.customer_id)
        .where(Message.id.in_({r.message_id for r in rows if r.message_id}))).all())
    items = [AiLogOut.model_validate(r, from_attributes=True).model_copy(update={
        "order_number": orders.get(r.order_id), "customer_name": msg_customer.get(r.message_id)}) for r in rows]
    return Page(items=items, total=total, page=page, size=size)
