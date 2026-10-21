"""Collections (SRS section 11, M8): payments, allocation, payment links, receipts, ageing.

Rules:
- an AI-read screenshot is never a payment until Accounts, Admin or Owner confirms it
- a UTR / cheque number already recorded for the tenant is flagged and not recorded twice
- confirmed payments go to the ledger and are allocated to the oldest unpaid invoices first (FIFO),
  unless the payment link was for specific invoices; anything left over stays on account
- cheques stay Pending clearance (not in the ledger) until cleared; a bounce after clearing reverses it
"""

import re
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    Customer,
    GatewayMode,
    Invoice,
    InvoiceStatus,
    LedgerKind,
    LinkStatus,
    MessageTemplate,
    Payment,
    PaymentAllocation,
    PaymentGatewayAccount,
    PaymentLink,
    PaymentMode,
    PaymentSource,
    PaymentStatus,
    SenderKind,
    Tenant,
    TemplateStatus,
    User,
)
from app.services import audit
from app.services import invoices as inv_svc
from app.services import orders as order_svc
from app.services.gateways import GatewayError, expiry, get_gateway
from app.services.gst import money

LIVE = (PaymentStatus.proposed, PaymentStatus.pending_clearance, PaymentStatus.confirmed)
NEEDS_REF = {PaymentMode.cheque: "cheque number", PaymentMode.neft: "UTR", PaymentMode.rtgs: "UTR",
             PaymentMode.upi: "UTR / UPI reference"}
MODE_LABEL = {PaymentMode.link: "payment link", PaymentMode.cash: "cash", PaymentMode.cheque: "cheque",
              PaymentMode.neft: "NEFT", PaymentMode.rtgs: "RTGS", PaymentMode.upi: "UPI", PaymentMode.other: "other"}


class PaymentError(Exception):
    def __init__(self, message: str, code: str = "invalid", http: int = 400):
        super().__init__(message)
        self.message = message
        self.code = code
        self.http = http


def norm_ref(ref: str | None) -> str | None:
    r = re.sub(r"\s+", "", (ref or "")).upper()
    return r or None


def find_duplicate(db: Session, tenant_id: uuid.UUID, reference: str | None,
                   exclude: uuid.UUID | None = None) -> Payment | None:
    """FR-M8-08: same UTR / cheque number already recorded (and not rejected or bounced)."""
    ref = norm_ref(reference)
    if not ref:
        return None
    q = select(Payment).where(Payment.tenant_id == tenant_id, Payment.reference == ref, Payment.status.in_(LIVE))
    if exclude:
        q = q.where(Payment.id != exclude)
    return db.scalar(q.limit(1))


def balance_due(inv: Invoice) -> Decimal:
    return money(inv.total - (inv.amount_paid or 0)) if inv.status == InvoiceStatus.issued else Decimal(0)


def unpaid_invoices(db: Session, customer: Customer) -> list[Invoice]:
    return list(db.scalars(select(Invoice).where(
        Invoice.tenant_id == customer.tenant_id, Invoice.customer_id == customer.id,
        Invoice.status == InvoiceStatus.issued, Invoice.total > Invoice.amount_paid,
    ).order_by(Invoice.due_date.asc().nulls_last(), Invoice.invoice_date, Invoice.created_at).with_for_update()))


# ---------------- buyer messages ----------------
def notify_buyer(db: Session, customer: Customer, text: str, template_name: str, params: list[str]):
    """Text inside the 24-hour window, else the approved utility template. Returns (message, note)."""
    from app.services.whatsapp import outbound
    from app.services.whatsapp.inbound import get_or_create_conversation

    try:
        outbound.get_account(db, customer.tenant_id)
        if customer.opted_out:
            return None, "Not sent: customer opted out of WhatsApp"
        conv = get_or_create_conversation(db, customer)
        if outbound.window_open(conv):
            msg = outbound.queue_text(db, customer, text, sender=SenderKind.system)
        else:
            tpl = db.scalar(select(MessageTemplate).where(
                MessageTemplate.tenant_id == customer.tenant_id, MessageTemplate.name == template_name,
                MessageTemplate.status == TemplateStatus.approved))
            if tpl is None:
                return None, f"Not sent: no chat in the last 24 hours and no approved '{template_name}' template"
            msg = outbound.queue_template(db, customer, tpl, params[: tpl.variable_count], sender=SenderKind.system)
        order_svc._pending(db).append(msg.id)
        return msg, None
    except outbound.SendBlocked as exc:
        return None, f"Not sent: {exc.message}"


def send_receipt(db: Session, p: Payment) -> None:
    """FR-M8-05: WhatsApp receipt for every recorded payment."""
    customer = db.get(Customer, p.customer_id)
    lines = []
    for a in p.allocations:
        inv = db.get(Invoice, a.invoice_id)
        lines.append(f"• {inv.number}: ₹{a.amount:,.2f}")
    bal = inv_svc.balance(db, customer)
    text = (f"Payment received, thank you! ₹{p.amount:,.2f} on {p.payment_date:%d %b %Y} by {MODE_LABEL[p.mode]}"
            + (f" (ref {p.reference})" if p.reference and p.mode != PaymentMode.link else "") + f". Receipt {p.number}.")
    if lines:
        text += "\nAdjusted against:\n" + "\n".join(lines)
    if p.unallocated and p.unallocated > 0:
        text += f"\n₹{p.unallocated:,.2f} kept as advance on your account."
    text += (f"\nBalance due now: ₹{bal:,.2f}." if bal > 0 else "\nYou have no dues with us now.")
    name = customer.contact_person or customer.business_name
    msg, note = notify_buyer(db, customer, text, "payment_receipt", [name, f"{p.amount:,.2f}", p.number])
    p.receipt_message_id = msg.id if msg else None
    p.receipt_note = note


# ---------------- recording ----------------
def allocate(db: Session, p: Payment, invoice_ids: list[uuid.UUID] | None = None) -> None:
    customer = db.get(Customer, p.customer_id)
    remaining = money(p.amount)
    pool = unpaid_invoices(db, customer)
    if invoice_ids:
        wanted = [i for i in pool if i.id in set(invoice_ids)]
        wanted.sort(key=lambda i: invoice_ids.index(i.id))
        pool = wanted + [i for i in pool if i.id not in set(invoice_ids)]  # surplus then goes FIFO
    for inv in pool:
        if remaining <= 0:
            break
        due = balance_due(inv)
        if due <= 0:
            continue
        take = min(due, remaining)
        p.allocations.append(PaymentAllocation(tenant_id=p.tenant_id, invoice_id=inv.id, amount=take))
        inv.amount_paid = money((inv.amount_paid or 0) + take)
        remaining = money(remaining - take)
    p.unallocated = remaining


def _apply(db: Session, user: User | None, p: Payment, invoice_ids: list[uuid.UUID] | None = None) -> None:
    """Money is in: ledger credit, allocation, receipt."""
    p.status = PaymentStatus.confirmed
    p.confirmed_at = datetime.now(UTC)
    p.confirmed_by_user_id = user.id if user else None
    inv_svc._ledger(db, p.tenant_id, p.customer_id, p.payment_date, LedgerKind.payment, ref_id=p.id,
                    ref_number=p.number, credit=p.amount,
                    narration=f"Payment by {MODE_LABEL[p.mode]}" + (f" ({p.reference})" if p.reference else ""))
    allocate(db, p, invoice_ids)
    db.flush()
    send_receipt(db, p)


def record_payment(db: Session, user: User | None, customer: Customer, *, amount: Decimal, mode: PaymentMode,
                   payment_date: date | None = None, reference: str | None = None, cheque_bank: str | None = None,
                   cheque_date: date | None = None, payer_name: str | None = None, notes: str | None = None,
                   invoice_ids: list[uuid.UUID] | None = None, source: PaymentSource = PaymentSource.manual,
                   link: PaymentLink | None = None, gateway_payment_id: str | None = None) -> Payment:
    if amount is None or amount <= 0:
        raise PaymentError("Amount must be more than zero")
    if mode in NEEDS_REF and not norm_ref(reference):
        raise PaymentError(f"Enter the {NEEDS_REF[mode]}")
    dup = find_duplicate(db, customer.tenant_id, reference)
    if dup is not None:
        raise PaymentError(f"{reference} is already recorded as {dup.number} (₹{dup.amount:,.2f}) — "
                           "not recorded again", "duplicate", 409)
    tenant = db.get(Tenant, customer.tenant_id)
    payment_date = payment_date or inv_svc.today()
    p = Payment(tenant_id=tenant.id, number=inv_svc.next_doc_number(db, tenant, "receipt", payment_date),
                customer_id=customer.id, amount=money(amount), payment_date=payment_date, mode=mode,
                status=PaymentStatus.pending_clearance if mode == PaymentMode.cheque else PaymentStatus.confirmed,
                source=source, reference=norm_ref(reference), cheque_bank=cheque_bank, cheque_date=cheque_date,
                payer_name=payer_name, notes=notes, link_id=link.id if link else None,
                gateway_payment_id=gateway_payment_id, created_by_user_id=user.id if user else None,
                unallocated=Decimal(0), allocations=[])  # set by allocate() once the money is in
    db.add(p)
    db.flush()
    audit.record(db, user, "payment", p.id, "create", new={"number": p.number, "amount": str(p.amount),
                                                           "mode": mode.value}, tenant_id=tenant.id)
    if p.status == PaymentStatus.confirmed:
        _apply(db, user, p, invoice_ids)
    elif invoice_ids:
        p.ai_extracted = {**(p.ai_extracted or {}), "invoice_ids": [str(i) for i in invoice_ids]}
    return p


def clear_cheque(db: Session, user: User, p: Payment) -> Payment:
    if p.status != PaymentStatus.pending_clearance:
        raise PaymentError("Only cheques pending clearance can be cleared", "invalid_transition", 409)
    ids = [uuid.UUID(i) for i in (p.ai_extracted or {}).get("invoice_ids", [])] or None
    _apply(db, user, p, ids)
    audit.record(db, user, "payment", p.id, "update", {"status": "pending_clearance"}, {"status": "confirmed"})
    return p


def bounce_cheque(db: Session, user: User, p: Payment, reason: str) -> Payment:
    if p.mode != PaymentMode.cheque or p.status not in (PaymentStatus.pending_clearance, PaymentStatus.confirmed):
        raise PaymentError("Only received cheques can be marked bounced", "invalid_transition", 409)
    if p.status == PaymentStatus.confirmed:
        for a in list(p.allocations):
            inv = db.get(Invoice, a.invoice_id)
            inv.amount_paid = money((inv.amount_paid or 0) - a.amount)
            p.allocations.remove(a)
        inv_svc._ledger(db, p.tenant_id, p.customer_id, inv_svc.today(), LedgerKind.payment_reversed, ref_id=p.id,
                        ref_number=p.number, debit=p.amount, narration=f"Cheque {p.reference} bounced: {reason}")
    old = p.status.value
    p.status = PaymentStatus.bounced
    p.status_reason = (reason or "Bounced")[:300]
    p.unallocated = Decimal(0)
    audit.record(db, user, "payment", p.id, "update", {"status": old}, {"status": "bounced", "reason": reason})
    return p


def confirm_proposal(db: Session, user: User, p: Payment, *, customer: Customer, amount: Decimal, mode: PaymentMode,
                     payment_date: date, reference: str | None, cheque_bank: str | None = None,
                     invoice_ids: list[uuid.UUID] | None = None) -> Payment:
    if p.status != PaymentStatus.proposed:
        raise PaymentError("Only proposed payments can be confirmed", "invalid_transition", 409)
    if amount <= 0:
        raise PaymentError("Amount must be more than zero")
    if mode in NEEDS_REF and not norm_ref(reference):
        raise PaymentError(f"Enter the {NEEDS_REF[mode]}")
    dup = find_duplicate(db, p.tenant_id, reference, exclude=p.id)
    if dup is not None:
        raise PaymentError(f"{reference} is already recorded as {dup.number}", "duplicate", 409)
    p.customer_id, p.amount, p.mode, p.payment_date = customer.id, money(amount), mode, payment_date
    p.reference, p.cheque_bank, p.unallocated = norm_ref(reference), cheque_bank, Decimal(0)
    if mode == PaymentMode.cheque:
        p.status = PaymentStatus.pending_clearance
        p.confirmed_by_user_id = user.id
        if invoice_ids:
            p.ai_extracted = {**(p.ai_extracted or {}), "invoice_ids": [str(i) for i in invoice_ids]}
    else:
        _apply(db, user, p, invoice_ids)
    audit.record(db, user, "payment", p.id, "update", {"status": "proposed"}, {"status": p.status.value})
    return p


def reject_proposal(db: Session, user: User, p: Payment, reason: str) -> Payment:
    if p.status != PaymentStatus.proposed:
        raise PaymentError("Only proposed payments can be rejected", "invalid_transition", 409)
    p.status = PaymentStatus.rejected
    p.status_reason = (reason or "Rejected")[:300]
    audit.record(db, user, "payment", p.id, "update", {"status": "proposed"}, {"status": "rejected"})
    return p


def propose_from_buyer(db: Session, customer: Customer, message_id: uuid.UUID, fields: dict) -> tuple[Payment | None, str]:
    """FR-M8-07: AI read a payment proof. Returns (proposal or None, reply text for the buyer)."""
    amount = fields.get("amount")
    ref = norm_ref(fields.get("reference"))
    dup = find_duplicate(db, customer.tenant_id, ref)
    if dup is not None:
        return None, (f"This payment (ref {ref}) is already recorded with us as receipt {dup.number}. Thank you!")
    tenant = db.get(Tenant, customer.tenant_id)
    mode = fields.get("mode") if fields.get("mode") in PaymentMode._value2member_map_ else (
        PaymentMode.cheque.value if fields.get("cheque") else PaymentMode.upi.value)
    p = Payment(tenant_id=tenant.id, number=inv_svc.next_doc_number(db, tenant, "receipt"), customer_id=customer.id,
                amount=money(Decimal(str(amount))) if amount else Decimal("0.00"),
                payment_date=fields.get("payment_date") or inv_svc.today(), mode=PaymentMode(mode),
                status=PaymentStatus.proposed, source=PaymentSource.ai_proof, reference=ref,
                payer_name=fields.get("payer_name"), message_id=message_id, ai_extracted=fields.get("raw") or {},
                unallocated=Decimal(0), allocations=[])
    db.add(p)
    db.flush()
    bits = [f"₹{p.amount:,.2f}" if p.amount else None, f"ref {ref}" if ref else None]
    detail = ", ".join(b for b in bits if b)
    return p, (f"Thanks! We've received your payment details{f' ({detail})' if detail else ''}. "
               "Our accounts team will confirm it shortly and send you a receipt.")


# ---------------- payment links ----------------
def gateway_account(db: Session, tenant_id: uuid.UUID) -> PaymentGatewayAccount | None:
    return db.scalar(select(PaymentGatewayAccount).where(PaymentGatewayAccount.tenant_id == tenant_id))


def create_link(db: Session, user: User | None, customer: Customer, *, amount: Decimal | None = None,
                invoice_ids: list[uuid.UUID] | None = None, purpose: str = "manual") -> PaymentLink:
    invoices = unpaid_invoices(db, customer)
    if invoice_ids:
        invoices = [i for i in invoices if i.id in set(invoice_ids)]
        if len(invoices) != len(set(invoice_ids)):
            raise PaymentError("Some of those invoices are not unpaid invoices of this customer")
    due = money(sum((balance_due(i) for i in invoices), Decimal(0)))
    amount = money(amount) if amount else due
    if amount <= 0:
        raise PaymentError("Nothing is due for this customer")
    account = gateway_account(db, customer.tenant_id)
    if account is not None and not account.is_active:
        account = None
    tenant = db.get(Tenant, customer.tenant_id)
    link = PaymentLink(tenant_id=tenant.id, customer_id=customer.id, amount=amount,
                       invoice_ids=[str(i.id) for i in invoices] if invoice_ids else None,
                       gateway=account.mode if account else GatewayMode.simulator, status=LinkStatus.created,
                       purpose=purpose, expires_at=expiry(7), created_by_user_id=user.id if user else None)
    db.add(link)
    db.flush()
    numbers = ", ".join(i.number for i in invoices[:5]) or "account balance"
    try:
        made = get_gateway(account).create_link(link.id, int(amount * 100), f"{tenant.name}: {numbers}",
                                                customer.business_name, customer.whatsapp_number, link.expires_at)
    except GatewayError as exc:
        raise PaymentError(exc.message, "gateway", 502) from None
    link.gateway_link_id, link.short_url = made.gateway_link_id, made.short_url
    return link


def link_text(db: Session, link: PaymentLink) -> str:
    tenant = db.get(Tenant, link.tenant_id)
    return (f"Pay ₹{link.amount:,.2f} to {tenant.name} securely by UPI, card or net banking: {link.short_url}"
            + (f"\nLink valid till {link.expires_at:%d %b %Y}." if link.expires_at else ""))


def send_link(db: Session, link: PaymentLink):
    customer = db.get(Customer, link.customer_id)
    name = customer.contact_person or customer.business_name
    return notify_buyer(db, customer, link_text(db, link), "payment_reminder",
                        [name, f"{link.amount:,.2f}", link.short_url or ""])


def link_paid(db: Session, link: PaymentLink, *, gateway_payment_id: str | None, amount: Decimal | None = None,
              method: str | None = None) -> Payment:
    """Gateway says the link was paid (webhook or simulator). Idempotent (FR-M8-04)."""
    existing = db.scalar(select(Payment).where(Payment.link_id == link.id, Payment.status == PaymentStatus.confirmed))
    if existing is not None:
        return existing
    if link.status in (LinkStatus.cancelled,):
        raise PaymentError("This link was cancelled", "invalid_transition", 409)
    customer = db.get(Customer, link.customer_id)
    ids = [uuid.UUID(i) for i in link.invoice_ids] if link.invoice_ids else None
    p = record_payment(db, None, customer, amount=money(amount) if amount else link.amount, mode=PaymentMode.link,
                       reference=gateway_payment_id, notes=f"Paid via link{f' ({method})' if method else ''}",
                       invoice_ids=ids, source=PaymentSource.gateway, link=link, gateway_payment_id=gateway_payment_id)
    link.status = LinkStatus.paid
    link.paid_at = datetime.now(UTC)
    return p


def cancel_link(db: Session, link: PaymentLink) -> PaymentLink:
    if link.status != LinkStatus.created:
        raise PaymentError("Only open links can be cancelled", "invalid_transition", 409)
    account = gateway_account(db, link.tenant_id)
    if link.gateway == GatewayMode.razorpay and link.gateway_link_id:
        try:
            get_gateway(account).cancel_link(link.gateway_link_id)
        except GatewayError as exc:
            raise PaymentError(exc.message, "gateway", 502) from None
    link.status = LinkStatus.cancelled
    return link


# ---------------- reports ----------------
BUCKETS = ["not_due", "d0_30", "d31_60", "d61_90", "d90_plus"]


def bucket(days_overdue: int) -> str:
    if days_overdue <= 0:
        return "not_due"
    if days_overdue <= 30:
        return "d0_30"
    if days_overdue <= 60:
        return "d31_60"
    if days_overdue <= 90:
        return "d61_90"
    return "d90_plus"


def ageing(db: Session, tenant_id: uuid.UUID, customer_ids: list[uuid.UUID] | None = None,
           as_of: date | None = None) -> list[dict]:
    """FR-M8-09: unpaid invoice balances by days past due, per customer."""
    as_of = as_of or inv_svc.today()
    q = select(Invoice).where(Invoice.tenant_id == tenant_id, Invoice.status == InvoiceStatus.issued,
                              Invoice.total > Invoice.amount_paid)
    if customer_ids is not None:
        q = q.where(Invoice.customer_id.in_(customer_ids))
    rows: dict[uuid.UUID, dict] = {}
    for inv in db.scalars(q):
        due = inv.due_date or inv.invoice_date
        r = rows.setdefault(inv.customer_id, {b: Decimal(0) for b in BUCKETS} | {"invoices": 0})
        r[bucket((as_of - due).days)] += balance_due(inv)
        r["invoices"] += 1
    customers = {c.id: c for c in db.scalars(select(Customer).where(Customer.id.in_(list(rows))))} if rows else {}
    out = []
    for cid, r in rows.items():
        c = customers[cid]
        total = money(sum((r[b] for b in BUCKETS), Decimal(0)))
        out.append({"customer_id": cid, "customer_name": c.business_name, "whatsapp_number": c.whatsapp_number,
                    "invoices": r["invoices"], **{b: money(r[b]) for b in BUCKETS}, "total": total,
                    "overdue": money(total - r["not_due"]), "reminders_paused": c.reminders_paused})
    out.sort(key=lambda x: x["overdue"], reverse=True)
    return out


def dashboard(db: Session, tenant_id: uuid.UUID, as_of: date | None = None) -> dict:
    """FR-M8-11."""
    from app.models import LedgerEntry

    as_of = as_of or inv_svc.today()
    month_start = as_of.replace(day=1)
    opening = db.scalar(select(func.coalesce(func.sum(Customer.opening_balance), 0)).where(Customer.tenant_id == tenant_id))
    ledger = db.scalar(select(func.coalesce(func.sum(LedgerEntry.debit - LedgerEntry.credit), 0))
                       .where(LedgerEntry.tenant_id == tenant_id))
    overdue = db.scalar(select(func.coalesce(func.sum(Invoice.total - Invoice.amount_paid), 0)).where(
        Invoice.tenant_id == tenant_id, Invoice.status == InvoiceStatus.issued, Invoice.due_date < as_of,
        Invoice.total > Invoice.amount_paid))
    by_mode = db.execute(select(Payment.mode, func.coalesce(func.sum(Payment.amount), 0)).where(
        Payment.tenant_id == tenant_id, Payment.status == PaymentStatus.confirmed, Payment.payment_date >= month_start,
        Payment.payment_date <= as_of).group_by(Payment.mode)).all()
    paid = {True: Decimal(0), False: Decimal(0)}
    for mode, amount in by_mode:
        paid[mode == PaymentMode.link] += Decimal(amount)
    pending = db.execute(select(Payment.status, func.count(), func.coalesce(func.sum(Payment.amount), 0)).where(
        Payment.tenant_id == tenant_id, Payment.status.in_([PaymentStatus.proposed, PaymentStatus.pending_clearance]))
        .group_by(Payment.status)).all()
    pend = {s.value: {"count": n, "amount": money(a)} for s, n, a in pending}
    via_link, offline = money(paid.get(True, 0)), money(paid.get(False, 0))
    return {"total_outstanding": money(Decimal(opening) + Decimal(ledger)), "overdue": money(overdue),
            "collected_this_month": money(via_link + offline), "collected_via_link": via_link,
            "collected_offline": offline,
            "to_confirm": pend.get("proposed", {"count": 0, "amount": Decimal("0.00")}),
            "cheques_pending": pend.get("pending_clearance", {"count": 0, "amount": Decimal("0.00")})}
