"""WhatsApp campaigns (SRS section 12, M9).

Flow: build a segment -> pick an approved MARKETING template and fill its variables -> test send to one number ->
send now or schedule. When sending starts, the segment is frozen into CampaignRecipient rows: opted-out buyers and
buyers without opt-in are skipped (and counted); the rest wait as "pending". dispatch() turns pending recipients into
WhatsApp messages, but never more than the number's Meta messaging limit allows in a rolling 24 hours; whatever is
left goes out on later runs (Celery beat, every minute). Delivery status comes from the Message rows (Meta webhooks).
Meta failures (e.g. too many marketing messages to that buyer) show with Meta's reason and are not retried.
"""

import logging
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import and_, distinct, func, or_, select
from sqlalchemy.orm import Session, aliased

from app.core.config import settings
from app.models import (
    Campaign,
    CampaignRecipient,
    CampaignStatus,
    Customer,
    CustomerStatus,
    Direction,
    LedgerEntry,
    Message,
    MessageStatus,
    MessageTemplate,
    MessageType,
    Order,
    OrderStatus,
    Product,
    RecipientStatus,
    SenderKind,
    TemplateCategory,
    TemplateStatus,
    Tenant,
    User,
    WhatsAppAccount,
)
from app.schemas.campaigns import FIELD_LABELS, Segment
from app.services import media
from app.services import orders as order_svc

log = logging.getLogger("pinesphere.campaigns")

ATTRIBUTION_DAYS = 7
# orders that count as "placed" in the report (a quote the buyer never confirmed does not)
PLACED = (OrderStatus.confirmed, OrderStatus.on_hold, OrderStatus.approved, OrderStatus.invoiced,
          OrderStatus.dispatched, OrderStatus.delivered)
TAP_PREFIX = "campaign:order:"
IMAGE_MIMES = {"image/jpeg", "image/png"}
MAX_IMAGE = 5 * 1024 * 1024
MAX_DOC = 16 * 1024 * 1024


class CampaignError(Exception):
    def __init__(self, message: str, code: str = "invalid", http: int = 400):
        super().__init__(message)
        self.message, self.code, self.http = message, code, http


def now() -> datetime:
    return datetime.now(UTC)


# ---------------------------------------------------------------- segment
def _outstanding_sq(tenant_id: uuid.UUID):
    return (select(LedgerEntry.customer_id, func.sum(LedgerEntry.debit - LedgerEntry.credit).label("net"))
            .where(LedgerEntry.tenant_id == tenant_id).group_by(LedgerEntry.customer_id).subquery())


def _last_order_sq(tenant_id: uuid.UUID):
    return (select(Order.customer_id, func.max(Order.created_at).label("last_at"))
            .where(Order.tenant_id == tenant_id, Order.status.in_(PLACED)).group_by(Order.customer_id).subquery())


def segment_rows(db: Session, tenant_id: uuid.UUID, seg: Segment) -> list[tuple[Customer, Decimal]]:
    """Active customers matching the segment, with their outstanding balance."""
    led = _outstanding_sq(tenant_id)
    outstanding = (func.coalesce(Customer.opening_balance, 0) + func.coalesce(led.c.net, 0)).label("outstanding")
    q = (select(Customer, outstanding).outerjoin(led, led.c.customer_id == Customer.id)
         .where(Customer.tenant_id == tenant_id, Customer.status == CustomerStatus.active))
    if seg.customer_types:
        q = q.where(Customer.customer_type.in_(seg.customer_types))
    if seg.tags:
        q = q.where(Customer.tags.overlap(seg.tags))
    if seg.cities:
        q = q.where(func.lower(Customer.city).in_([c.lower() for c in seg.cities]))
    if seg.states:
        q = q.where(func.lower(Customer.state).in_([s.lower() for s in seg.states]))
    if seg.assigned_user_ids:
        q = q.where(Customer.assigned_user_id.in_(seg.assigned_user_ids))
    if seg.no_order_days or seg.ordered_within_days:
        last = _last_order_sq(tenant_id)
        q = q.outerjoin(last, last.c.customer_id == Customer.id)
        if seg.no_order_days:
            q = q.where(or_(last.c.last_at.is_(None), last.c.last_at < now() - timedelta(days=seg.no_order_days)))
        if seg.ordered_within_days:
            q = q.where(last.c.last_at >= now() - timedelta(days=seg.ordered_within_days))
    if seg.min_outstanding is not None:
        q = q.where(outstanding >= seg.min_outstanding)
    if seg.max_outstanding is not None:
        q = q.where(outstanding <= seg.max_outstanding)
    return [(c, Decimal(o or 0)) for c, o in db.execute(q.order_by(Customer.business_name)).all()]


def exclusion(c: Customer) -> str | None:
    """FR-M9-05 / FR-M2-06: never message a buyer who opted out or never opted in."""
    if c.opted_out:
        return "Opted out of WhatsApp messages"
    if not c.whatsapp_opt_in:
        return "No WhatsApp opt-in recorded"
    return None


def used_last_24h(db: Session, tenant_id: uuid.UUID) -> int:
    """Buyers who got a business-initiated (template) message in the last 24 hours: what Meta's limit counts."""
    return db.scalar(select(func.count(distinct(Message.customer_id))).where(
        Message.tenant_id == tenant_id, Message.direction == Direction.outbound,
        Message.msg_type == MessageType.template, Message.status != MessageStatus.failed,
        Message.created_at >= now() - timedelta(hours=24))) or 0


def capacity(db: Session, tenant: Tenant) -> int:
    return max(0, (tenant.wa_daily_limit or 0) - used_last_24h(db, tenant.id))


def preview(db: Session, tenant: Tenant, seg: Segment) -> dict:
    rows = segment_rows(db, tenant.id, seg)
    opted_out = sum(1 for c, _ in rows if c.opted_out)
    no_opt_in = sum(1 for c, _ in rows if not c.opted_out and not c.whatsapp_opt_in)
    eligible = len(rows) - opted_out - no_opt_in
    rate = Decimal(tenant.campaign_message_rate or 0)
    return {
        "matched": len(rows), "eligible": eligible, "opted_out": opted_out, "no_opt_in": no_opt_in,
        "rate": rate, "estimated_cost": (rate * eligible).quantize(Decimal("0.01")),
        "sample": [c.business_name for c, _ in rows if exclusion(c) is None][:5],
        "capacity_now": capacity(db, tenant), "daily_limit": tenant.wa_daily_limit,
    }


# ---------------------------------------------------------------- message building
def field_value(db: Session, c: Customer, field: str, outstanding: Decimal) -> str:
    if field == "first_name":
        return (c.contact_person or c.business_name).split()[0]
    if field == "contact_name":
        return c.contact_person or c.business_name
    if field == "business_name":
        return c.business_name
    if field == "city":
        return c.city or ""
    if field == "state":
        return c.state or ""
    if field == "outstanding":
        return f"₹{outstanding:,.2f}"
    if field == "sales_person":
        u = db.get(User, c.assigned_user_id) if c.assigned_user_id else None
        return u.name if u else ""
    return ""


def params_for(db: Session, campaign: Campaign, c: Customer, outstanding: Decimal) -> list[str]:
    out = []
    for v in campaign.variables or []:
        val = field_value(db, c, v["value"], outstanding) if v.get("kind") == "field" else v.get("value", "")
        out.append(val.strip() or "-")  # Meta rejects empty parameters
    return out


def _template(db: Session, campaign: Campaign) -> MessageTemplate | None:
    return db.get(MessageTemplate, campaign.template_id) if campaign.template_id else None


def needs_media(tpl: MessageTemplate | None) -> str | None:
    return tpl.header_format if tpl and tpl.header_format in ("IMAGE", "DOCUMENT") else None


def validate_ready(db: Session, campaign: Campaign) -> MessageTemplate:
    tpl = _template(db, campaign)
    if tpl is None:
        raise CampaignError("Pick a message template")
    if tpl.category != TemplateCategory.marketing:
        raise CampaignError("Campaigns use MARKETING templates. Pick a marketing template.")
    if tpl.status != TemplateStatus.approved:
        raise CampaignError(f"Template '{tpl.name}' is not approved by WhatsApp yet", "template_not_approved")
    vars_ = campaign.variables or []
    if len(vars_) != tpl.variable_count:
        raise CampaignError(f"Fill all {tpl.variable_count} template variable(s)")
    for i, v in enumerate(vars_, start=1):
        if v.get("kind") == "field" and v.get("value") not in FIELD_LABELS:
            raise CampaignError(f"Variable {{{{{i}}}}}: pick a customer field")
        if v.get("kind") != "field" and not (v.get("value") or "").strip():
            raise CampaignError(f"Variable {{{{{i}}}}} is empty")
    kind = needs_media(tpl)
    if kind and not campaign.media_path:
        raise CampaignError("This template has an image/PDF header. Attach the " +
                            ("image" if kind == "IMAGE" else "PDF") + " first.")
    if campaign.order_button_index is not None and campaign.order_button_index >= len(tpl.buttons or []):
        raise CampaignError("The 'Order now' button is not on this template")
    return tpl


def _media_id(db: Session, campaign: Campaign) -> str:
    """Upload the attachment once per campaign; every recipient reuses the media id."""
    if campaign.wa_media_id:
        return campaign.wa_media_id
    from app.services.whatsapp.client import get_client

    account = db.scalar(select(WhatsAppAccount).where(WhatsAppAccount.tenant_id == campaign.tenant_id))
    campaign.wa_media_id = get_client(account).upload_media(
        media.read(campaign.media_path), campaign.media_mime or "application/octet-stream",
        campaign.media_filename or "file")
    return campaign.wa_media_id


def queue_for(db: Session, campaign: Campaign, tpl: MessageTemplate, c: Customer, outstanding: Decimal) -> Message:
    """Create the WhatsApp template message for one buyer (raises SendBlocked)."""
    from app.services.whatsapp import outbound

    msg = outbound.queue_template(db, c, tpl, params_for(db, campaign, c, outstanding), sender=SenderKind.system)
    payload = dict(msg.payload)
    tpl_part = dict(payload["template"])
    comps = list(tpl_part.get("components", []))
    kind = needs_media(tpl)
    if kind:
        key = "image" if kind == "IMAGE" else "document"
        part = {"id": _media_id(db, campaign)}
        if key == "document":
            part["filename"] = campaign.media_filename or "file.pdf"
        comps.insert(0, {"type": "header", "parameters": [{"type": key, key: part}]})
        msg.media_path, msg.media_mime, msg.media_filename = (campaign.media_path, campaign.media_mime,
                                                              campaign.media_filename)
    buttons = [dict(b) for b in (tpl.buttons or [])]
    if campaign.order_button_index is not None and campaign.order_button_index < len(buttons):
        tap = f"{TAP_PREFIX}{campaign.id}"
        comps.append({"type": "button", "sub_type": "quick_reply", "index": str(campaign.order_button_index),
                      "parameters": [{"type": "payload", "payload": tap}]})
        buttons[campaign.order_button_index]["payload"] = tap
    tpl_part["components"] = comps
    payload["template"] = tpl_part
    payload["_buttons"] = buttons
    payload["_campaign_id"] = str(campaign.id)
    msg.payload = payload
    return msg


# ---------------------------------------------------------------- actions
def test_send(db: Session, user: User, campaign: Campaign, number: str) -> Message:
    """FR-M9-08: the exact message to one number (must be a customer, e.g. your own number saved as one)."""
    from app.core.validators import normalize_phone
    from app.services import invoices as inv_svc
    from app.services.whatsapp import outbound

    tpl = validate_ready(db, campaign)
    try:
        number = normalize_phone(number)
    except ValueError:
        raise CampaignError("Enter a valid WhatsApp number") from None
    c = db.scalar(select(Customer).where(Customer.tenant_id == campaign.tenant_id, Customer.whatsapp_number == number))
    if c is None:
        raise CampaignError("Add this number as a customer first (for example your own number, tagged 'test'), "
                            "with WhatsApp opt-in", "not_found")
    try:
        msg = queue_for(db, campaign, tpl, c, inv_svc.balance(db, c))
    except outbound.SendBlocked as exc:
        raise CampaignError(f"Test not sent: {exc.message}", exc.code) from None
    except Exception as exc:  # media upload failure
        raise CampaignError(f"Test not sent: {exc}") from None
    campaign.last_test_at, campaign.last_test_to = now(), number
    order_svc._pending(db).append(msg.id)
    return msg


def _freeze_recipients(db: Session, campaign: Campaign) -> int:
    rows = segment_rows(db, campaign.tenant_id, Segment.model_validate(campaign.segment or {}))
    for c, _ in rows:
        reason = exclusion(c)
        db.add(CampaignRecipient(tenant_id=campaign.tenant_id, campaign_id=campaign.id, customer_id=c.id,
                                 status=RecipientStatus.skipped if reason else RecipientStatus.pending,
                                 skip_reason=reason))
    db.flush()
    return sum(1 for c, _ in rows if exclusion(c) is None)


def start(db: Session, user: User | None, campaign: Campaign) -> Campaign:
    if campaign.status not in (CampaignStatus.draft, CampaignStatus.scheduled):
        raise CampaignError("This campaign has already been sent", "invalid_state", 409)
    validate_ready(db, campaign)
    tenant = db.get(Tenant, campaign.tenant_id)
    campaign.status = CampaignStatus.sending
    campaign.started_at = now()
    campaign.message_rate = tenant.campaign_message_rate
    if user is not None:
        campaign.sent_by_user_id = user.id
    _freeze_recipients(db, campaign)
    dispatch(db, campaign)
    return campaign


def schedule(db: Session, user: User, campaign: Campaign, when: datetime) -> Campaign:
    if campaign.status not in (CampaignStatus.draft, CampaignStatus.scheduled):
        raise CampaignError("Only a draft campaign can be scheduled", "invalid_state", 409)
    if when.tzinfo is None:
        raise CampaignError("Send the time with its timezone")
    if when <= now() + timedelta(minutes=1):
        raise CampaignError("Pick a time in the future")
    validate_ready(db, campaign)
    campaign.scheduled_at = when
    campaign.status = CampaignStatus.scheduled
    campaign.sent_by_user_id = user.id
    return campaign


def unschedule(db: Session, campaign: Campaign) -> Campaign:
    if campaign.status != CampaignStatus.scheduled:
        raise CampaignError("This campaign is not scheduled", "invalid_state", 409)
    campaign.status = CampaignStatus.draft
    campaign.scheduled_at = None
    return campaign


def cancel(db: Session, campaign: Campaign) -> Campaign:
    if campaign.status not in (CampaignStatus.scheduled, CampaignStatus.sending):
        raise CampaignError("Only a scheduled or sending campaign can be cancelled", "invalid_state", 409)
    for r in db.scalars(select(CampaignRecipient).where(CampaignRecipient.campaign_id == campaign.id,
                                                       CampaignRecipient.status == RecipientStatus.pending)):
        r.status, r.skip_reason = RecipientStatus.skipped, "Campaign cancelled before this message went out"
    campaign.status = CampaignStatus.cancelled
    campaign.cancelled_at = now()
    campaign.waiting_note = None
    return campaign


def dispatch(db: Session, campaign: Campaign, batch: int | None = None) -> int:
    """Queue as many pending recipients as the messaging limit allows. Returns how many were queued."""
    from app.services import invoices as inv_svc
    from app.services.whatsapp import outbound

    if campaign.status != CampaignStatus.sending:
        return 0
    tenant = db.get(Tenant, campaign.tenant_id)
    pending_q = select(CampaignRecipient).where(CampaignRecipient.campaign_id == campaign.id,
                                               CampaignRecipient.status == RecipientStatus.pending)
    tpl = _template(db, campaign)
    if tpl is None or tpl.status != TemplateStatus.approved:
        reason = "Template is no longer approved by WhatsApp"
        for r in db.scalars(pending_q):
            r.status, r.skip_reason = RecipientStatus.failed, reason
        _finish(campaign)
        return 0
    room = min(capacity(db, tenant), batch or settings.CAMPAIGN_BATCH_SIZE)
    queued = 0
    for r in db.scalars(pending_q.order_by(CampaignRecipient.created_at).limit(room)).all():
        c = db.get(Customer, r.customer_id)
        reason = exclusion(c)  # buyer may have opted out since the campaign started
        if reason:
            r.status, r.skip_reason = RecipientStatus.skipped, reason
            continue
        try:
            msg = queue_for(db, campaign, tpl, c, inv_svc.balance(db, c))
        except outbound.SendBlocked as exc:
            r.status, r.skip_reason = RecipientStatus.failed, exc.message
            continue
        except Exception as exc:  # e.g. the attachment could not be uploaded: stop, try again next run
            log.warning("Campaign %s paused: %s", campaign.id, exc)
            campaign.waiting_note = f"Sending paused: {str(exc)[:200]}. Will retry automatically."
            return queued
        r.status, r.message_id, r.queued_at = RecipientStatus.queued, msg.id, now()
        order_svc._pending(db).append(msg.id)
        queued += 1
    db.flush()
    left = db.scalar(select(func.count()).select_from(pending_q.subquery())) or 0
    if left == 0:
        _finish(campaign)
    else:
        campaign.waiting_note = (
            f"{left} message(s) waiting: your WhatsApp number can message {tenant.wa_daily_limit} buyers per 24 hours. "
            "The rest go out automatically as the limit frees up." if capacity(db, tenant) == 0
            else f"{left} message(s) still being queued.")
    return queued


def _finish(campaign: Campaign) -> None:
    campaign.status = CampaignStatus.completed
    campaign.completed_at = now()
    campaign.waiting_note = None


def run_due(db: Session, tenant_id: uuid.UUID | None = None) -> int:
    """Start scheduled campaigns whose time has come and continue the ones waiting on the limit."""
    q = select(Campaign).where(or_(
        and_(Campaign.status == CampaignStatus.scheduled, Campaign.scheduled_at <= now()),
        Campaign.status == CampaignStatus.sending))
    if tenant_id:
        q = q.where(Campaign.tenant_id == tenant_id)
    n = 0
    for campaign in db.scalars(q.order_by(Campaign.scheduled_at.nulls_last(), Campaign.created_at)).all():
        try:
            if campaign.status == CampaignStatus.scheduled:
                start(db, None, campaign)
            else:
                dispatch(db, campaign)
            n += 1
        except CampaignError as exc:  # e.g. template rejected after scheduling
            campaign.status = CampaignStatus.cancelled
            campaign.cancelled_at = now()
            campaign.waiting_note = f"Not sent: {exc.message}"
    return n


# ---------------------------------------------------------------- "Order now" (FR-M9-07)
def parse_tap(reply_id: str | None) -> uuid.UUID | None:
    if not reply_id or not reply_id.startswith(TAP_PREFIX):
        return None
    try:
        return uuid.UUID(reply_id[len(TAP_PREFIX):])
    except ValueError:
        return None


def tap_reply(db: Session, campaign: Campaign | None, c: Customer) -> str:
    from app.services.pricing import PricingEngine

    name = (c.contact_person or c.business_name).split()[0]
    products = []
    if campaign and campaign.product_ids:
        found = {p.id: p for p in db.scalars(select(Product).where(
            Product.tenant_id == c.tenant_id, Product.id.in_(campaign.product_ids), Product.is_active.is_(True)))}
        products = [found[i] for i in campaign.product_ids if i in found]
    if not products:
        return (f"Thanks {name}! Send your order here with item names and quantities, e.g. \"10 bags atta, "
                "5 boxes soap\". You can also send a photo of your list or a voice note.")
    engine = PricingEngine(db, c)
    rows = []
    for i, p in enumerate(products, start=1):
        price = engine.price_line(p, Decimal(str(p.min_order_qty or 1)))
        row = f"{i}. {p.name}: ₹{price['unit_price']:,.2f} per {p.uom}"
        if price.get("scheme_name"):
            row += f" (offer: {price['scheme_name']})"
        if p.stock_status.value == "out_of_stock":
            row += " (out of stock)"
        rows.append(row)
    return (f"Thanks {name}! Here are the items from this offer:\n" + "\n".join(rows) +
            f"\n\nReply with the items and quantities you need, e.g. \"10 {products[0].name}\", "
            "and we'll send you a quote to confirm.")


def handle_tap(db: Session, msg: Message, campaign_id: uuid.UUID) -> None:
    """Buyer tapped "Order now": record it and start the order flow with the campaign's products."""
    from app.models import Conversation
    from app.services.whatsapp import outbound

    c = db.get(Customer, msg.customer_id)
    campaign = db.scalar(select(Campaign).where(Campaign.id == campaign_id, Campaign.tenant_id == msg.tenant_id))
    if campaign is not None:
        r = db.scalar(select(CampaignRecipient).where(CampaignRecipient.campaign_id == campaign.id,
                                                     CampaignRecipient.customer_id == c.id))
        if r is not None and r.order_tapped_at is None:
            r.order_tapped_at = msg.created_at or now()
    conv = db.get(Conversation, msg.conversation_id)
    if conv is not None and not conv.bot_enabled:
        return  # a staff member is handling this chat
    try:
        out = outbound.queue_text(db, c, tap_reply(db, campaign, c), sender=SenderKind.bot)
        order_svc._pending(db).append(out.id)
    except outbound.SendBlocked as exc:
        log.info("Campaign tap reply not sent: %s", exc.message)


# ---------------------------------------------------------------- report (FR-M9-06)
def _replied_expr(window: timedelta):
    """Buyer sent us anything within 7 days after the campaign message."""
    m = aliased(Message)
    return select(m.id).where(
        m.customer_id == CampaignRecipient.customer_id, m.direction == Direction.inbound,
        m.created_at > CampaignRecipient.queued_at, m.created_at <= CampaignRecipient.queued_at + window,
    ).correlate(CampaignRecipient).exists()


def _orders_sq(campaign_id: uuid.UUID):
    """Placed orders from each recipient within 7 days of their message."""
    window = timedelta(days=ATTRIBUTION_DAYS)
    return (select(CampaignRecipient.id.label("rid"), func.count(Order.id).label("n"),
                   func.coalesce(func.sum(Order.grand_total), 0).label("value"))
            .join(Order, and_(Order.customer_id == CampaignRecipient.customer_id,
                              Order.tenant_id == CampaignRecipient.tenant_id,
                              Order.created_at >= CampaignRecipient.queued_at,
                              Order.created_at < CampaignRecipient.queued_at + window,
                              Order.status.in_(PLACED)))
            .where(CampaignRecipient.campaign_id == campaign_id, CampaignRecipient.queued_at.is_not(None))
            .group_by(CampaignRecipient.id).subquery())


def stats(db: Session, campaign: Campaign) -> dict:
    base = select(CampaignRecipient).where(CampaignRecipient.campaign_id == campaign.id)
    by_status = dict(db.execute(select(CampaignRecipient.status, func.count()).where(
        CampaignRecipient.campaign_id == campaign.id).group_by(CampaignRecipient.status)).all())
    msg_status = dict(db.execute(
        select(Message.status, func.count()).join(CampaignRecipient, CampaignRecipient.message_id == Message.id)
        .where(CampaignRecipient.campaign_id == campaign.id).group_by(Message.status)).all())
    skipped_reasons = db.execute(
        select(CampaignRecipient.skip_reason, func.count()).where(
            CampaignRecipient.campaign_id == campaign.id, CampaignRecipient.status == RecipientStatus.skipped)
        .group_by(CampaignRecipient.skip_reason)).all()
    fail_rows = db.execute(
        select(Message.error_message, Message.error_code, func.count())
        .join(CampaignRecipient, CampaignRecipient.message_id == Message.id)
        .where(CampaignRecipient.campaign_id == campaign.id, Message.status == MessageStatus.failed)
        .group_by(Message.error_message, Message.error_code)).all()
    not_queued = db.execute(
        select(CampaignRecipient.skip_reason, func.count()).where(
            CampaignRecipient.campaign_id == campaign.id, CampaignRecipient.status == RecipientStatus.failed)
        .group_by(CampaignRecipient.skip_reason)).all()
    replied = db.scalar(select(func.count()).select_from(base.where(
        CampaignRecipient.queued_at.is_not(None), _replied_expr(timedelta(days=ATTRIBUTION_DAYS))).subquery())) or 0
    taps = db.scalar(select(func.count()).select_from(base.where(
        CampaignRecipient.order_tapped_at.is_not(None)).subquery())) or 0
    osq = _orders_sq(campaign.id)
    orders, value, buyers = db.execute(select(func.coalesce(func.sum(osq.c.n), 0),
                                              func.coalesce(func.sum(osq.c.value), 0), func.count(osq.c.rid))).one()

    ms = {k.value if hasattr(k, "value") else k: v for k, v in msg_status.items()}
    rs = {k.value if hasattr(k, "value") else k: v for k, v in by_status.items()}
    read = ms.get("read", 0)
    delivered = ms.get("delivered", 0) + read
    sent = ms.get("sent", 0) + delivered
    failed = ms.get("failed", 0) + rs.get("failed", 0)
    targeted = sum(rs.values())
    rate = Decimal(campaign.message_rate or 0)
    return {
        "targeted": targeted,
        "excluded": rs.get("skipped", 0),
        "excluded_reasons": [{"reason": r or "Skipped", "count": n} for r, n in skipped_reasons],
        "waiting": rs.get("pending", 0) + ms.get("queued", 0),
        "sent": sent, "delivered": delivered, "read": read, "failed": failed,
        "failed_reasons": [{"reason": (m or "Failed") + (f" (code {code})" if code else ""), "count": n}
                           for m, code, n in fail_rows]
                          + [{"reason": r or "Not sent", "count": n} for r, n in not_queued],
        "replied": replied, "order_taps": taps,
        "orders": int(orders), "order_value": Decimal(value).quantize(Decimal("0.01")), "ordering_buyers": buyers,
        "messages_charged": sent,
        "estimated_cost": (rate * (rs.get("pending", 0) + rs.get("queued", 0))).quantize(Decimal("0.01")),
        "attribution_days": ATTRIBUTION_DAYS,
    }


def recipients(db: Session, campaign: Campaign, view: str, page: int, size: int) -> tuple[list[dict], int]:
    osq = _orders_sq(campaign.id)
    replied = _replied_expr(timedelta(days=ATTRIBUTION_DAYS))
    q = (select(CampaignRecipient, Customer, Message, osq.c.n, osq.c.value,
                and_(CampaignRecipient.queued_at.is_not(None), replied).label("replied"))
         .join(Customer, Customer.id == CampaignRecipient.customer_id)
         .outerjoin(Message, Message.id == CampaignRecipient.message_id)
         .outerjoin(osq, osq.c.rid == CampaignRecipient.id)
         .where(CampaignRecipient.campaign_id == campaign.id))
    if view == "failed":
        q = q.where(or_(CampaignRecipient.status == RecipientStatus.failed, Message.status == MessageStatus.failed))
    elif view == "skipped":
        q = q.where(CampaignRecipient.status == RecipientStatus.skipped)
    elif view == "read":
        q = q.where(Message.status == MessageStatus.read)
    elif view == "tapped":
        q = q.where(CampaignRecipient.order_tapped_at.is_not(None))
    elif view == "ordered":
        q = q.where(osq.c.n > 0)
    elif view == "replied":
        q = q.where(CampaignRecipient.queued_at.is_not(None), replied)
    total = db.scalar(select(func.count()).select_from(q.subquery())) or 0
    rows = []
    for r, c, m, n, value, did_reply in db.execute(
            q.order_by(Customer.business_name).offset((page - 1) * size).limit(size)).all():
        if r.status in (RecipientStatus.skipped, RecipientStatus.failed, RecipientStatus.pending):
            delivery = r.status.value
        else:
            delivery = m.status.value if m else "queued"
        reason = r.skip_reason or (m.error_message if m is not None and m.status == MessageStatus.failed else None)
        rows.append({
            "id": r.id, "customer_id": c.id, "customer_name": c.business_name, "whatsapp_number": c.whatsapp_number,
            "status": r.status, "delivery": delivery, "reason": reason, "queued_at": r.queued_at,
            "replied": bool(did_reply), "order_tapped": r.order_tapped_at is not None,
            "orders": int(n or 0), "order_value": Decimal(value or 0),
        })
    return rows, total
