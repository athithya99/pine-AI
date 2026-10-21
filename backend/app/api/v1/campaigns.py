"""Campaigns (M9). Owner and Admin only: campaigns cover all customers and cost money per message."""

import uuid

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.deps import MANAGERS, require_roles
from app.models import (
    Campaign,
    CampaignStatus,
    Customer,
    CustomerStatus,
    MessageTemplate,
    Product,
    Role,
    TemplateCategory,
    Tenant,
    User,
)
from app.schemas.campaigns import (
    FIELD_LABELS,
    CampaignIn,
    CampaignOut,
    CampaignRow,
    RecipientRow,
    ScheduleIn,
    Segment,
    SegmentPreview,
    SendIn,
    TestIn,
)
from app.schemas.common import Page
from app.services import audit, media
from app.services import campaigns as svc
from app.services import orders as order_svc

router = APIRouter(prefix="/campaigns", tags=["Campaigns"])
managers = require_roles(*MANAGERS)


def _err(exc: svc.CampaignError) -> HTTPException:
    return HTTPException(exc.http, exc.message)


def _catch_up(db: Session, tenant_id: uuid.UUID) -> None:
    """Without a Celery beat (local development) due campaigns start when the campaign screens are opened."""
    if settings.CELERY_EAGER and svc.run_due(db, tenant_id):
        order_svc.commit_and_send(db)


def _get(db: Session, user: User, campaign_id: uuid.UUID, *, lock: bool = False) -> Campaign:
    q = select(Campaign).where(Campaign.id == campaign_id, Campaign.tenant_id == user.tenant_id)
    c = db.scalar(q.with_for_update() if lock else q)
    if c is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Campaign not found")
    return c


def _out(db: Session, c: Campaign) -> CampaignOut:
    tpl = db.get(MessageTemplate, c.template_id) if c.template_id else None
    names = {u.id: u.name for u in db.scalars(select(User).where(
        User.id.in_([i for i in (c.created_by_user_id, c.sent_by_user_id) if i])))}
    products = []
    if c.product_ids:
        found = {p.id: p for p in db.scalars(select(Product).where(Product.id.in_(c.product_ids)))}
        products = [{"id": p.id, "sku": p.sku, "name": p.name} for i in c.product_ids if (p := found.get(i))]
    started = c.status in (CampaignStatus.sending, CampaignStatus.completed) or (
        c.status == CampaignStatus.cancelled and c.started_at is not None)
    return CampaignOut(
        id=c.id, name=c.name, status=c.status, template_id=c.template_id,
        template_name=tpl.name if tpl else None, template_body=tpl.body if tpl else None,
        template_header_format=tpl.header_format if tpl else None, template_buttons=tpl.buttons if tpl else None,
        variables=c.variables or [], segment=Segment.model_validate(c.segment or {}),
        has_media=bool(c.media_path), media_filename=c.media_filename, media_mime=c.media_mime,
        order_button_index=c.order_button_index, products=products,
        scheduled_at=c.scheduled_at, started_at=c.started_at, completed_at=c.completed_at,
        cancelled_at=c.cancelled_at, waiting_note=c.waiting_note, last_test_at=c.last_test_at,
        last_test_to=c.last_test_to, created_by_name=names.get(c.created_by_user_id),
        sent_by_name=names.get(c.sent_by_user_id), created_at=c.created_at,
        stats=svc.stats(db, c) if started else None,
    )


def _apply(db: Session, user: User, c: Campaign, body: CampaignIn) -> None:
    if body.template_id:
        tpl = db.scalar(select(MessageTemplate).where(MessageTemplate.id == body.template_id,
                                                      MessageTemplate.tenant_id == user.tenant_id))
        if tpl is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Template not found")
        if tpl.category != TemplateCategory.marketing:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Campaigns use MARKETING templates")
        if c.template_id != tpl.id and (c.media_path and svc.needs_media(tpl) != svc.needs_media(
                db.get(MessageTemplate, c.template_id) if c.template_id else None)):
            c.media_path = c.media_mime = c.media_filename = c.wa_media_id = None
    if body.product_ids:
        ids = set(db.scalars(select(Product.id).where(Product.tenant_id == user.tenant_id,
                                                      Product.id.in_(body.product_ids))))
        if len(ids) != len(set(body.product_ids)):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Product not found")
    c.name = body.name.strip()
    c.template_id = body.template_id
    c.variables = [v.model_dump() for v in body.variables]
    c.segment = body.segment.model_dump(mode="json", exclude_defaults=True)
    c.order_button_index = body.order_button_index
    c.product_ids = list(dict.fromkeys(body.product_ids))


# ---------------------------------------------------------------- setup helpers
@router.get("/options")
def options(db: Session = Depends(get_db), user: User = Depends(managers)):
    """Everything the campaign form needs in one call."""
    tid = user.tenant_id
    tags = db.scalars(select(func.unnest(Customer.tags)).where(
        Customer.tenant_id == tid, Customer.status == CustomerStatus.active).distinct()).all()
    cities = db.scalars(select(Customer.city).where(
        Customer.tenant_id == tid, Customer.city.is_not(None), Customer.city != "").distinct()).all()
    states = db.scalars(select(Customer.state).where(
        Customer.tenant_id == tid, Customer.state.is_not(None), Customer.state != "").distinct()).all()
    sales = db.scalars(select(User).where(User.tenant_id == tid, User.role == Role.sales, User.is_active.is_(True))
                       .order_by(User.name)).all()
    templates = db.scalars(select(MessageTemplate).where(
        MessageTemplate.tenant_id == tid, MessageTemplate.category == TemplateCategory.marketing)
        .order_by(MessageTemplate.name)).all()
    tenant = db.get(Tenant, tid)
    return {
        "fields": [{"key": k, "label": v} for k, v in FIELD_LABELS.items()],
        "tags": sorted(t for t in tags if t), "cities": sorted(cities), "states": sorted(states),
        "sales_users": [{"id": u.id, "name": u.name} for u in sales],
        "templates": [{"id": t.id, "name": t.name, "language": t.language, "status": t.status, "body": t.body,
                       "header_format": t.header_format, "header_text": t.header_text, "footer": t.footer,
                       "buttons": t.buttons or [], "variable_count": t.variable_count} for t in templates],
        "rate": tenant.campaign_message_rate, "daily_limit": tenant.wa_daily_limit,
    }


@router.post("/preview", response_model=SegmentPreview)
def preview(body: Segment, db: Session = Depends(get_db), user: User = Depends(managers)):
    """FR-M9-01 / 05: how many buyers match, how many are excluded, and the estimated Meta charge."""
    return svc.preview(db, db.get(Tenant, user.tenant_id), body)


# ---------------------------------------------------------------- CRUD
@router.get("", response_model=Page[CampaignRow])
def list_campaigns(status_filter: CampaignStatus | None = Query(default=None, alias="status"),
                   q: str | None = None, page: int = Query(default=1, ge=1), size: int = Query(default=25, le=100),
                   db: Session = Depends(get_db), user: User = Depends(managers)):
    _catch_up(db, user.tenant_id)
    stmt = select(Campaign).where(Campaign.tenant_id == user.tenant_id)
    if status_filter:
        stmt = stmt.where(Campaign.status == status_filter)
    if q:
        stmt = stmt.where(Campaign.name.ilike(f"%{q.strip()}%"))
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    items = []
    for c in db.scalars(stmt.order_by(Campaign.created_at.desc()).offset((page - 1) * size).limit(size)).all():
        tpl = db.get(MessageTemplate, c.template_id) if c.template_id else None
        st = svc.stats(db, c) if c.started_at else None
        items.append(CampaignRow(
            id=c.id, name=c.name, status=c.status, template_name=tpl.name if tpl else None,
            scheduled_at=c.scheduled_at, started_at=c.started_at, created_at=c.created_at,
            targeted=st["targeted"] if st else 0, sent=st["sent"] if st else 0, read=st["read"] if st else 0,
            orders=st["orders"] if st else 0, order_value=st["order_value"] if st else 0))
    return Page(items=items, total=total, page=page, size=size)


@router.post("", response_model=CampaignOut, status_code=status.HTTP_201_CREATED)
def create_campaign(body: CampaignIn, db: Session = Depends(get_db), user: User = Depends(managers)):
    c = Campaign(tenant_id=user.tenant_id, created_by_user_id=user.id, status=CampaignStatus.draft,
                 name=body.name, variables=[], segment={}, product_ids=[])
    _apply(db, user, c, body)
    db.add(c)
    db.flush()
    audit.record(db, user, "campaign", c.id, "create", new=audit.snapshot(c))
    db.commit()
    return _out(db, c)


@router.get("/{campaign_id}", response_model=CampaignOut)
def get_campaign(campaign_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(managers)):
    _catch_up(db, user.tenant_id)
    return _out(db, _get(db, user, campaign_id))


@router.put("/{campaign_id}", response_model=CampaignOut)
def update_campaign(campaign_id: uuid.UUID, body: CampaignIn, db: Session = Depends(get_db),
                    user: User = Depends(managers)):
    c = _get(db, user, campaign_id, lock=True)
    if c.status != CampaignStatus.draft:
        raise HTTPException(status.HTTP_409_CONFLICT, "Only a draft can be edited. Unschedule it first.")
    old = audit.snapshot(c)
    _apply(db, user, c, body)
    audit.record(db, user, "campaign", c.id, "update", old, audit.snapshot(c))
    db.commit()
    return _out(db, c)


@router.delete("/{campaign_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_campaign(campaign_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(managers)):
    c = _get(db, user, campaign_id, lock=True)
    if c.status != CampaignStatus.draft:
        raise HTTPException(status.HTTP_409_CONFLICT, "Only a draft can be deleted")
    audit.record(db, user, "campaign", c.id, "delete", audit.snapshot(c))
    db.delete(c)
    db.commit()


@router.post("/{campaign_id}/duplicate", response_model=CampaignOut, status_code=status.HTTP_201_CREATED)
def duplicate(campaign_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(managers)):
    src = _get(db, user, campaign_id)
    c = Campaign(tenant_id=user.tenant_id, created_by_user_id=user.id, status=CampaignStatus.draft,
                 name=f"{src.name} (copy)"[:150], template_id=src.template_id, variables=src.variables,
                 segment=src.segment, order_button_index=src.order_button_index, product_ids=src.product_ids,
                 media_mime=src.media_mime, media_filename=src.media_filename)
    if src.media_path:
        c.media_path = media.save(user.tenant_id, media.read(src.media_path), src.media_mime, src.media_filename)
    db.add(c)
    db.flush()
    audit.record(db, user, "campaign", c.id, "create", new=audit.snapshot(c))
    db.commit()
    return _out(db, c)


# ---------------------------------------------------------------- attachment
@router.post("/{campaign_id}/media", response_model=CampaignOut)
async def upload_media(campaign_id: uuid.UUID, file: UploadFile = File(...), db: Session = Depends(get_db),
                       user: User = Depends(managers)):
    c = _get(db, user, campaign_id, lock=True)
    if c.status != CampaignStatus.draft:
        raise HTTPException(status.HTTP_409_CONFLICT, "Only a draft can be edited")
    tpl = db.get(MessageTemplate, c.template_id) if c.template_id else None
    kind = svc.needs_media(tpl)
    if kind is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "This template has no image/PDF header. Pick a template with a media header in WhatsApp.")
    content = await file.read()
    mime = file.content_type or ""
    if kind == "IMAGE" and (mime not in svc.IMAGE_MIMES or len(content) > svc.MAX_IMAGE):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Attach a JPG or PNG image up to 5 MB")
    if kind == "DOCUMENT" and (mime != "application/pdf" or len(content) > svc.MAX_DOC):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Attach a PDF up to 16 MB")
    c.media_path = media.save(user.tenant_id, content, mime, file.filename)
    c.media_mime, c.media_filename, c.wa_media_id = mime, file.filename, None
    db.commit()
    return _out(db, c)


@router.delete("/{campaign_id}/media", response_model=CampaignOut)
def remove_media(campaign_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(managers)):
    c = _get(db, user, campaign_id, lock=True)
    if c.status != CampaignStatus.draft:
        raise HTTPException(status.HTTP_409_CONFLICT, "Only a draft can be edited")
    c.media_path = c.media_mime = c.media_filename = c.wa_media_id = None
    db.commit()
    return _out(db, c)


@router.get("/{campaign_id}/media")
def get_media(campaign_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(managers)):
    c = _get(db, user, campaign_id)
    if not c.media_path:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No attachment")
    return FileResponse(media.full_path(c.media_path), media_type=c.media_mime or "application/octet-stream",
                        filename=c.media_filename or None)


# ---------------------------------------------------------------- sending
@router.post("/{campaign_id}/test")
def test_send(campaign_id: uuid.UUID, body: TestIn, db: Session = Depends(get_db), user: User = Depends(managers)):
    c = _get(db, user, campaign_id, lock=True)
    try:
        msg = svc.test_send(db, user, c, body.whatsapp_number)
    except svc.CampaignError as exc:
        db.rollback()
        raise _err(exc) from None
    order_svc.commit_and_send(db)
    db.refresh(msg)
    return {"message": f"Test sent to {c.last_test_to}", "status": msg.status.value, "error": msg.error_message}


@router.post("/{campaign_id}/send", response_model=CampaignOut)
def send_now(campaign_id: uuid.UUID, body: SendIn, db: Session = Depends(get_db), user: User = Depends(managers)):
    c = _get(db, user, campaign_id, lock=True)
    p = svc.preview(db, db.get(Tenant, user.tenant_id), Segment.model_validate(c.segment or {}))
    if p["eligible"] != body.confirmed_count:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            f"The segment changed: it now has {p['eligible']} buyers. "
                            "Check the count and confirm again.")
    if p["eligible"] == 0:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "No buyers in this segment can receive WhatsApp messages")
    try:
        svc.start(db, user, c)
    except svc.CampaignError as exc:
        db.rollback()
        raise _err(exc) from None
    audit.record(db, user, "campaign", c.id, "update", {"status": "draft"}, {"status": c.status.value})
    order_svc.commit_and_send(db)
    return _out(db, c)


@router.post("/{campaign_id}/schedule", response_model=CampaignOut)
def schedule(campaign_id: uuid.UUID, body: ScheduleIn, db: Session = Depends(get_db), user: User = Depends(managers)):
    c = _get(db, user, campaign_id, lock=True)
    try:
        svc.schedule(db, user, c, body.scheduled_at)
    except svc.CampaignError as exc:
        db.rollback()
        raise _err(exc) from None
    audit.record(db, user, "campaign", c.id, "update", {"status": "draft"},
                 {"status": c.status.value, "scheduled_at": c.scheduled_at.isoformat()})
    db.commit()
    return _out(db, c)


@router.post("/{campaign_id}/unschedule", response_model=CampaignOut)
def unschedule(campaign_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(managers)):
    c = _get(db, user, campaign_id, lock=True)
    try:
        svc.unschedule(db, c)
    except svc.CampaignError as exc:
        raise _err(exc) from None
    db.commit()
    return _out(db, c)


@router.post("/{campaign_id}/cancel", response_model=CampaignOut)
def cancel(campaign_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(managers)):
    c = _get(db, user, campaign_id, lock=True)
    try:
        svc.cancel(db, c)
    except svc.CampaignError as exc:
        raise _err(exc) from None
    audit.record(db, user, "campaign", c.id, "update", None, {"status": "cancelled"})
    db.commit()
    return _out(db, c)


@router.get("/{campaign_id}/recipients", response_model=Page[RecipientRow])
def recipients(campaign_id: uuid.UUID, view: str = Query(default="all"), page: int = Query(default=1, ge=1),
               size: int = Query(default=50, le=200), db: Session = Depends(get_db), user: User = Depends(managers)):
    c = _get(db, user, campaign_id)
    rows, total = svc.recipients(db, c, view, page, size)
    return Page(items=rows, total=total, page=page, size=size)

