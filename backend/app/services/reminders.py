"""Payment reminders (FR-M8-01, 02, 10).

Runs daily (Celery beat at REMINDER_HOUR_IST). For each unpaid invoice whose (today - due date) matches one of the
company's steps (default 3 days before, due date, 7 and 15 days after), the buyer gets ONE message listing all
their unpaid invoices and the total due, with a payment link for that amount. A ReminderLog row per invoice per
step guarantees no step is ever sent twice. Paused invoices/customers, opted-out buyers and buyers without
opt-in are skipped (the reason is logged).
"""

from collections import defaultdict
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Customer, Invoice, InvoiceStatus, ReminderLog, Tenant
from app.services import invoices as inv_svc
from app.services import payments as pay_svc
from app.services.whatsapp.inbound import get_or_create_conversation


def step_label(offset: int) -> str:
    if offset < 0:
        return f"due in {-offset} day{'s' if offset != -1 else ''}"
    if offset == 0:
        return "due today"
    return f"overdue by {offset} day{'s' if offset != 1 else ''}"


def reminder_text(tenant: Tenant, customer: Customer, invoices: list[Invoice], total, link_url: str | None,
                  on: date) -> str:
    name = (customer.contact_person or customer.business_name).split()[0]
    rows = []
    for inv in invoices[:10]:
        due = inv.due_date or inv.invoice_date
        rows.append(f"• {inv.number}: ₹{pay_svc.balance_due(inv):,.2f} ({step_label((on - due).days)}, {due:%d %b})")
    more = f"\n…and {len(invoices) - 10} more" if len(invoices) > 10 else ""
    pay = f"\n\nPay now: {link_url}" if link_url else ""
    return (f"Hi {name}, a payment reminder from {tenant.name}.\n" + "\n".join(rows) + more
            + f"\n\nTotal due: ₹{total:,.2f}{pay}\n\nIgnore this if you have already paid.")


def run_reminders(db: Session, tenant: Tenant, on: date | None = None) -> dict:
    on = on or inv_svc.today()
    summary = {"customers": 0, "sent": 0, "skipped": 0}
    if not tenant.reminders_enabled:
        return summary
    steps = set(tenant.reminder_offsets or [])
    invoices = db.scalars(select(Invoice).where(
        Invoice.tenant_id == tenant.id, Invoice.status == InvoiceStatus.issued, Invoice.total > Invoice.amount_paid,
        Invoice.reminders_paused.is_(False))).all()
    already = {(r.invoice_id, r.offset_days) for r in db.scalars(select(ReminderLog).where(
        ReminderLog.tenant_id == tenant.id, ReminderLog.invoice_id.in_([i.id for i in invoices])))} if invoices else set()
    due_now: dict = defaultdict(list)  # customer -> [(invoice, step)]
    for inv in invoices:
        step = (on - (inv.due_date or inv.invoice_date)).days
        if step in steps and (inv.id, step) not in already:
            due_now[inv.customer_id].append((inv, step))

    for customer_id, hits in due_now.items():
        customer = db.get(Customer, customer_id)
        summary["customers"] += 1
        note, msg, link = None, None, None
        if customer.reminders_paused:
            note = "Skipped: reminders paused for this customer"
        elif customer.opted_out:
            note = "Skipped: customer opted out of WhatsApp"
        elif not customer.whatsapp_opt_in and not _window_open(db, customer):
            note = "Skipped: customer has not opted in to WhatsApp messages"
        else:
            unpaid = pay_svc.unpaid_invoices(db, customer)
            unpaid = [i for i in unpaid if not i.reminders_paused]
            total = sum((pay_svc.balance_due(i) for i in unpaid), 0)
            try:
                link = pay_svc.create_link(db, None, customer, amount=total, purpose="reminder")
            except pay_svc.PaymentError as exc:
                note = f"Link not created: {exc.message}"
            name = customer.contact_person or customer.business_name
            msg, send_note = pay_svc.notify_buyer(
                db, customer, reminder_text(tenant, customer, unpaid, total, link.short_url if link else None, on),
                "payment_reminder", [name, f"{total:,.2f}", link.short_url if link else ""])
            note = send_note or note
        for inv, step in hits:
            db.add(ReminderLog(tenant_id=tenant.id, invoice_id=inv.id, customer_id=customer.id, offset_days=step,
                               run_date=on, message_id=msg.id if msg else None, link_id=link.id if link else None,
                               note=note))
        summary["sent" if msg else "skipped"] += 1
    db.flush()
    return summary


def _window_open(db: Session, customer: Customer) -> bool:
    from app.services.whatsapp.outbound import window_open

    return window_open(get_or_create_conversation(db, customer))
