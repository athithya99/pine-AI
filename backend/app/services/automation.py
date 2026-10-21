"""Owner rules (FR-M10-09) and the daily summary (FR-M10-08). Both run from Celery beat every few minutes."""

import logging
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    AutomationRule,
    Customer,
    DailySummary,
    Invoice,
    InvoiceStatus,
    MessageTemplate,
    Role,
    TemplateStatus,
    Tenant,
    TenantStatus,
    User,
    WhatsAppAccount,
)
from app.services import analytics as an

log = logging.getLogger("pinesphere.automation")


# ---------------------------------------------------------------- owner rules
def run_rule(db: Session, rule: AutomationRule, on: date) -> str:
    """Remind every buyer with an invoice more than N days overdue (one message per buyer, all unpaid invoices)."""
    from app.services import payments as pay_svc
    from app.services.reminders import _window_open, reminder_text

    tenant = db.get(Tenant, rule.tenant_id)
    min_days = int((rule.params or {}).get("min_days") or 0)
    due = func.coalesce(Invoice.due_date, Invoice.invoice_date)
    invs = db.scalars(select(Invoice).where(
        Invoice.tenant_id == rule.tenant_id, Invoice.status == InvoiceStatus.issued, Invoice.total > Invoice.amount_paid,
        Invoice.reminders_paused.is_(False), due < on - timedelta(days=min_days))).all()
    by_customer: dict = defaultdict(list)
    for inv in invs:
        by_customer[inv.customer_id].append(inv)
    sent = skipped = 0
    for cid in by_customer:
        c = db.get(Customer, cid)
        if c.reminders_paused or c.opted_out or (not c.whatsapp_opt_in and not _window_open(db, c)):
            skipped += 1
            continue
        unpaid = [i for i in pay_svc.unpaid_invoices(db, c) if not i.reminders_paused]
        total = sum((pay_svc.balance_due(i) for i in unpaid), 0)
        try:
            link = pay_svc.create_link(db, None, c, amount=total, purpose="reminder")
        except pay_svc.PaymentError:
            link = None
        msg, _ = pay_svc.notify_buyer(
            db, c, reminder_text(tenant, c, unpaid, total, link.short_url if link else None, on), "payment_reminder",
            [c.contact_person or c.business_name, f"{total:,.2f}", link.short_url if link else ""])
        if msg:
            sent += 1
        else:
            skipped += 1
    rule.last_run_on = on
    rule.last_run_summary = f"{on:%d %b}: {sent} reminder(s) sent, {skipped} skipped"
    return rule.last_run_summary


def run_due_rules(db: Session, now: datetime | None = None) -> int:
    now = (now or datetime.now(UTC)).astimezone(an.IST)
    on = now.date()
    n = 0
    for rule in db.scalars(select(AutomationRule).where(AutomationRule.is_active.is_(True))).all():
        tenant = db.get(Tenant, rule.tenant_id)
        if tenant.status != TenantStatus.active or rule.last_run_on == on:
            continue
        if on.weekday() in (rule.weekdays or []) and now.hour >= rule.hour:
            run_rule(db, rule, on)
            n += 1
    return n


# ---------------------------------------------------------------- daily summary
def summary_recipient(db: Session, tenant: Tenant) -> str | None:
    if tenant.daily_summary_to:
        return tenant.daily_summary_to
    owner = db.scalar(select(User).where(User.tenant_id == tenant.id, User.role == Role.owner,
                                         User.is_active.is_(True), User.mobile.is_not(None)).order_by(User.created_at))
    return owner.mobile if owner else None


def send_summary_whatsapp(db: Session, tenant: Tenant, s: DailySummary) -> None:
    """To the owner's own WhatsApp (not a customer chat): approved `daily_summary` template, else plain text.

    Template `daily_summary` (utility), body e.g.: "Summary for {{1}}: orders {{2}}, collections {{3}}, newly
    overdue {{4}}, approvals waiting {{5}}, chats waiting {{6}}." Plain text only reaches the owner if they messaged
    the business number in the last 24 hours (Meta rule)."""
    from app.services.whatsapp.client import WhatsAppError, get_client

    to = summary_recipient(db, tenant)
    s.sent_to = to
    if not to:
        s.send_error = "No WhatsApp number: add the owner's mobile, or set one in Company settings"
        return
    account = db.scalar(select(WhatsAppAccount).where(WhatsAppAccount.tenant_id == tenant.id,
                                                      WhatsAppAccount.is_active.is_(True)))
    if account is None:
        s.send_error = "WhatsApp number not connected"
        return
    f = s.data
    tpl = db.scalar(select(MessageTemplate).where(MessageTemplate.tenant_id == tenant.id,
                                                  MessageTemplate.name == "daily_summary",
                                                  MessageTemplate.status == TemplateStatus.approved))
    if tpl is not None:
        values = [f"{date.fromisoformat(f['date']):%d %b}", f"{f['orders']} ({an.inr(f['order_value'])})",
                  an.inr(f["collections"]), f"{f['new_overdue_invoices']} ({an.inr(f['new_overdue_amount'])})",
                  str(f["pending_approvals"]), str(f["unanswered_chats"])][: tpl.variable_count]
        body = {"type": "template", "template": {"name": tpl.name, "language": {"code": tpl.language}, "components": [
            {"type": "body", "parameters": [{"type": "text", "text": v} for v in values]}] if values else []}}
    else:
        body = {"type": "text", "text": {"body": s.text, "preview_url": False}}
    try:
        get_client(account).send(to, body)
        s.sent_at = datetime.now(UTC)
        s.send_error = None
    except WhatsAppError as exc:
        s.send_error = exc.message[:500]
    except Exception as exc:  # network
        s.send_error = str(exc)[:500]


def make_summary(db: Session, tenant: Tenant, on: date, *, send: bool = True) -> DailySummary:
    s = db.scalar(select(DailySummary).where(DailySummary.tenant_id == tenant.id, DailySummary.summary_date == on))
    figures = an.daily_figures(db, tenant.id, on)
    if s is None:
        s = DailySummary(tenant_id=tenant.id, summary_date=on, data=figures, text=an.daily_text(tenant.name, figures))
        db.add(s)
    else:
        s.data, s.text = figures, an.daily_text(tenant.name, figures)
    db.flush()
    if send:
        send_summary_whatsapp(db, tenant, s)
    return s


def run_due_summaries(db: Session, now: datetime | None = None) -> int:
    """Called every minute: each company gets today's summary once, as soon as its set time has passed."""
    now = (now or datetime.now(UTC)).astimezone(an.IST)
    on = now.date()
    done = set(db.scalars(select(DailySummary.tenant_id).where(DailySummary.summary_date == on)))
    n = 0
    for tenant in db.scalars(select(Tenant).where(Tenant.status == TenantStatus.active,
                                                  Tenant.daily_summary_enabled.is_(True))).all():
        if tenant.id in done:
            continue
        hh, mm = (int(x) for x in (tenant.daily_summary_time or "09:00").split(":"))
        if (now.hour, now.minute) < (hh, mm):
            continue
        try:
            make_summary(db, tenant, on)
            db.commit()
            n += 1
        except Exception:
            db.rollback()
            log.exception("Daily summary failed for %s", tenant.id)
    return n
