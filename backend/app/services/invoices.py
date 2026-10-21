"""Invoices, credit notes and the customer ledger (SRS section 10, M7).

Rules:
- an uploaded invoice stays in Review (not shared, not in the ledger) until staff confirm the AI-read fields
- a saved invoice is never edited, only cancelled with a reason; cancelling adds a reversing ledger row
- balance = opening balance + invoices - payments - credit notes
"""

import re
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.models import (
    CreditNote,
    CreditNoteStatus,
    Customer,
    DocSequence,
    Invoice,
    InvoiceLine,
    InvoiceSource,
    InvoiceStatus,
    LedgerEntry,
    LedgerKind,
    Order,
    OrderStatus,
    SenderKind,
    Tenant,
    User,
)
from app.services import audit, media, pdfs
from app.services import orders as order_svc
from app.services.gst import is_inter_state, money, split_tax, state_name
from app.services.orders import IST, financial_year


class InvoiceError(Exception):
    def __init__(self, message: str, code: str = "invalid", http: int = 400):
        super().__init__(message)
        self.message = message
        self.code = code
        self.http = http


def today() -> date:
    return datetime.now(IST).date()


# ---------------- numbering ----------------
def next_doc_number(db: Session, tenant: Tenant, kind: str, on: date | None = None) -> str:
    """INV/26-27/0001 - unique per tenant per financial year, at most 16 characters (FR-M7-04)."""
    on = on or today()
    fy = financial_year(on, tenant.fy_start_month or 4)
    db.execute(pg_insert(DocSequence).values(id=uuid.uuid4(), tenant_id=tenant.id, kind=kind, fy=fy, last_number=0)
               .on_conflict_do_nothing(constraint="uq_doc_seq"))
    seq = db.scalar(select(DocSequence).where(DocSequence.tenant_id == tenant.id, DocSequence.kind == kind,
                                              DocSequence.fy == fy).with_for_update())
    seq.last_number += 1
    prefix = {"invoice": tenant.invoice_prefix, "credit_note": tenant.credit_note_prefix,
              "receipt": tenant.receipt_prefix}.get(kind) or "DOC"
    return f"{prefix}/{fy}/{seq.last_number:04d}"


def number_taken(db: Session, tenant_id: uuid.UUID, number: str, exclude: uuid.UUID | None = None) -> bool:
    q = select(Invoice.id).where(Invoice.tenant_id == tenant_id, func.upper(Invoice.number) == number.strip().upper())
    if exclude:
        q = q.where(Invoice.id != exclude)
    return db.scalar(q) is not None


def norm_number(s: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", (s or "").upper())


# ---------------- ledger ----------------
def _ledger(db: Session, tenant_id, customer_id, entry_date: date, kind: LedgerKind, *, ref_id=None,
            ref_number=None, debit=Decimal(0), credit=Decimal(0), narration=None) -> LedgerEntry:
    e = LedgerEntry(tenant_id=tenant_id, customer_id=customer_id, entry_date=entry_date, kind=kind, ref_id=ref_id,
                    ref_number=ref_number, debit=money(debit), credit=money(credit), narration=narration)
    db.add(e)
    return e


def balance(db: Session, customer: Customer, as_of: date | None = None) -> Decimal:
    q = select(func.coalesce(func.sum(LedgerEntry.debit - LedgerEntry.credit), 0)).where(
        LedgerEntry.tenant_id == customer.tenant_id, LedgerEntry.customer_id == customer.id)
    if as_of:
        q = q.where(LedgerEntry.entry_date <= as_of)
    return money(Decimal(customer.opening_balance or 0) + Decimal(db.scalar(q)))


def fy_start(tenant: Tenant, on: date | None = None) -> date:
    on = on or today()
    m = tenant.fy_start_month or 4
    y = on.year if on.month >= m else on.year - 1
    return date(y, m, 1)


def statement(db: Session, customer: Customer, date_from: date | None = None, date_to: date | None = None) -> dict:
    tenant = db.get(Tenant, customer.tenant_id)
    date_from = date_from or fy_start(tenant)
    date_to = date_to or today()
    opening = balance(db, customer, date_from - timedelta(days=1))
    rows = db.scalars(select(LedgerEntry).where(
        LedgerEntry.tenant_id == customer.tenant_id, LedgerEntry.customer_id == customer.id,
        LedgerEntry.entry_date >= date_from, LedgerEntry.entry_date <= date_to,
    ).order_by(LedgerEntry.entry_date, LedgerEntry.created_at)).all()
    running = opening
    entries = []
    for r in rows:
        running = money(running + r.debit - r.credit)
        entries.append({"id": r.id, "entry_date": r.entry_date, "kind": r.kind.value, "ref_id": r.ref_id,
                        "ref_number": r.ref_number, "narration": r.narration, "debit": r.debit, "credit": r.credit,
                        "balance": running})
    return {"customer_id": customer.id, "date_from": date_from, "date_to": date_to, "opening": opening,
            "entries": entries, "total_debit": money(sum((r.debit for r in rows), Decimal(0))),
            "total_credit": money(sum((r.credit for r in rows), Decimal(0))), "closing": running}


# ---------------- PDFs & sending ----------------
def store_pdf(db: Session, inv: Invoice) -> None:
    tenant = db.get(Tenant, inv.tenant_id)
    customer = db.get(Customer, inv.customer_id)
    order = db.get(Order, inv.order_id) if inv.order_id else None
    content = pdfs.invoice_pdf(inv, tenant, customer, order.number if order else None)
    name = f"{inv.number.replace('/', '-')}.pdf"
    inv.pdf_path = media.save(inv.tenant_id, content, "application/pdf", name)
    inv.pdf_name = name


def send_document(db: Session, customer: Customer, content: bytes, filename: str, caption: str):
    """Queue a PDF to the buyer. Returns (message, note). Never raises."""
    from app.services.whatsapp import outbound
    from app.services.whatsapp.inbound import get_or_create_conversation

    try:
        outbound.get_account(db, customer.tenant_id)
        if customer.opted_out:
            return None, "Not sent: customer opted out of WhatsApp"
        conv = get_or_create_conversation(db, customer)
        if not outbound.window_open(conv):
            return None, ("Not sent: no chat with the buyer in the last 24 hours. WhatsApp only allows documents "
                          "inside that window; send it after the buyer messages you.")
        msg = outbound.queue_media(db, customer, content, "application/pdf", filename, caption[:1024],
                                   sender=SenderKind.system)
        order_svc._pending(db).append(msg.id)
        return msg, None
    except outbound.SendBlocked as exc:
        return None, f"Not sent: {exc.message}"


def send_invoice(db: Session, inv: Invoice, caption: str | None = None):
    if inv.status != InvoiceStatus.issued:
        raise InvoiceError("Only issued invoices can be shared")
    if not inv.pdf_path:
        raise InvoiceError("This invoice has no PDF yet. Attach the PDF first.")
    customer = db.get(Customer, inv.customer_id)
    caption = caption or (f"Invoice {inv.number} dated {inv.invoice_date:%d %b %Y} for ₹{inv.total:,.2f}"
                          + (f", due by {inv.due_date:%d %b %Y}" if inv.due_date else "") + ".")
    msg, note = send_document(db, customer, media.read(inv.pdf_path), inv.pdf_name or f"{inv.number}.pdf", caption)
    if msg is not None:
        inv.sent_message_id = msg.id
        inv.send_note = None
    else:
        inv.send_note = note
    return msg, note


# ---------------- invoices ----------------
def _place(tenant: Tenant, customer: Customer) -> tuple[str | None, bool]:
    pos = state_name(customer.gstin, customer.state) or tenant.state
    return pos, is_inter_state(state_name(tenant.gstin, tenant.state), pos)


def _set_totals(inv: Invoice, taxable: Decimal, cgst: Decimal, sgst: Decimal, igst: Decimal) -> None:
    inv.taxable_value = money(taxable)
    inv.cgst, inv.sgst, inv.igst = money(cgst), money(sgst), money(igst)
    inv.tax_total = money(cgst + sgst + igst)
    exact = inv.taxable_value + inv.tax_total
    inv.total = exact.quantize(Decimal("1"), rounding="ROUND_HALF_UP")
    inv.round_off = money(inv.total - exact)


def _post_invoice(db: Session, inv: Invoice) -> None:
    order = db.get(Order, inv.order_id) if inv.order_id else None
    _ledger(db, inv.tenant_id, inv.customer_id, inv.invoice_date, LedgerKind.invoice, ref_id=inv.id,
            ref_number=inv.number, debit=inv.total,
            narration=f"Invoice {inv.number}" + (f" (order {order.number})" if order else ""))


def due_for(customer: Customer, invoice_date: date, stated: date | None = None) -> date:
    """FR-M7-06: invoice date + credit days, unless the invoice states a due date."""
    return stated or invoice_date + timedelta(days=customer.credit_days or 0)


def create_from_order(db: Session, user: User, order: Order, *, mode: str, number: str | None = None,
                      invoice_date: date | None = None, invoiced: dict[uuid.UUID, Decimal] | None = None,
                      notes: str | None = None) -> Invoice:
    """FR-M7-03 / FR-M6-09. mode=generate makes our GST invoice + PDF; mode=erp records the ERP invoice number."""
    if order.status != OrderStatus.approved:
        raise InvoiceError(f"Cannot move order from {order_svc.label(order.status)} to Invoiced",
                           "invalid_transition", 409)
    tenant = db.get(Tenant, order.tenant_id)
    customer = db.get(Customer, order.customer_id)
    invoice_date = invoice_date or today()
    if mode == "erp":
        if not number or not number.strip():
            raise InvoiceError("Enter the invoice number from your billing software")
        number = number.strip()
        if number_taken(db, tenant.id, number):
            raise InvoiceError(f"Invoice number {number} is already used")
    elif mode == "generate":
        if not tenant.gstin:
            raise InvoiceError("Add your company GSTIN in Settings → Company before generating invoices")
    else:
        raise InvoiceError("Unknown invoice mode")
    invoiced = invoiced or {}
    if set(invoiced) - {l.id for l in order.lines}:
        raise InvoiceError("Unknown order line")
    if all(Decimal(invoiced.get(l.id, l.quantity)) <= 0 for l in order.lines):
        raise InvoiceError("Invoice at least one item")

    pos, inter = _place(tenant, customer)
    inv = Invoice(tenant_id=tenant.id, customer_id=customer.id, order_id=order.id,
                  source=InvoiceSource.generated if mode == "generate" else InvoiceSource.erp,
                  status=InvoiceStatus.issued, invoice_date=invoice_date, due_date=due_for(customer, invoice_date),
                  seller_gstin=tenant.gstin, buyer_gstin=customer.gstin, place_of_supply=pos, inter_state=inter,
                  notes=notes, created_by_user_id=user.id, confirmed_by_user_id=user.id,
                  confirmed_at=datetime.now(UTC), lines=[])
    inv.number = number if mode == "erp" else next_doc_number(db, tenant, "invoice", invoice_date)

    # lines follow the order's prices; a lower invoiced quantity scales the line (short supply)
    taxable = cgst = sgst = igst = Decimal(0)
    pos_no = 0
    for l in order.lines:
        q = Decimal(invoiced.get(l.id, l.quantity))
        if q < 0 or q > l.quantity:
            raise InvoiceError(f"Invoiced quantity for {l.name} must be between 0 and {pdfs.qty(l.quantity)}")
        if q == 0:
            continue
        share = q / l.quantity
        discount = money(l.discount * share)
        line_taxable = money(l.unit_price * q - discount)
        c, s, i = split_tax(line_taxable, l.gst_rate, inter)
        free = (l.free_qty * share).to_integral_value(rounding="ROUND_DOWN") if l.free_qty else Decimal(0)
        pos_no += 1
        inv.lines.append(InvoiceLine(
            tenant_id=tenant.id, position=pos_no, product_id=l.product_id, description=l.name, sku=l.sku,
            hsn_code=l.hsn_code, uom=l.uom, quantity=q, free_qty=free, rate=l.unit_price, discount=discount,
            taxable_value=line_taxable, gst_rate=l.gst_rate, cgst=c, sgst=s, igst=i,
            total=money(line_taxable + c + s + i)))
        taxable += line_taxable
        cgst, sgst, igst = cgst + c, sgst + s, igst + i
    _set_totals(inv, taxable, cgst, sgst, igst)
    db.add(inv)
    db.flush()

    send_pdf = mode == "generate" and tenant.invoice_auto_send
    ev = order_svc.mark_invoiced(db, user, order, inv.number, invoice_date,
                                 {lid: Decimal(q) for lid, q in invoiced.items()}, notify=not send_pdf)
    order.invoice_id = inv.id
    if mode == "generate":
        store_pdf(db, inv)
    _post_invoice(db, inv)
    audit.record(db, user, "invoice", inv.id, "create",
                 new={"number": inv.number, "order": order.number, "total": str(inv.total)})
    if send_pdf:
        msg, note = send_invoice(db, inv, order_svc.status_text(order, OrderStatus.invoiced)
                                 + f" Total ₹{inv.total:,.2f}" + (f", due by {inv.due_date:%d %b %Y}." if inv.due_date else "."))
        ev.message_id = msg.id if msg else None
        ev.notify_note = note
    return inv


def issue_manual(db: Session, user: User | None, inv: Invoice, *, customer: Customer, number: str,
                 invoice_date: date, due_date: date | None, taxable: Decimal, tax: Decimal, total: Decimal,
                 send: bool) -> Invoice:
    """Confirm an uploaded invoice or save an imported row: number, customer and amounts as stated."""
    tenant = db.get(Tenant, inv.tenant_id)
    if number_taken(db, tenant.id, number, exclude=inv.id):
        raise InvoiceError(f"Invoice number {number} is already used")
    if total <= 0:
        raise InvoiceError("Total must be more than zero")
    pos, inter = _place(tenant, customer)
    inv.customer_id = customer.id
    inv.number = number.strip()
    inv.invoice_date = invoice_date
    inv.due_date = due_for(customer, invoice_date, due_date)
    inv.place_of_supply, inv.inter_state = pos, inter
    inv.seller_gstin, inv.buyer_gstin = tenant.gstin, customer.gstin
    tax = money(tax)
    inv.taxable_value = money(taxable)
    if inter:
        inv.cgst = inv.sgst = Decimal("0.00")
        inv.igst = tax
    else:
        inv.cgst = money(tax / 2)
        inv.sgst = money(tax - inv.cgst)
        inv.igst = Decimal("0.00")
    inv.tax_total = tax
    inv.total = money(total)
    inv.round_off = money(inv.total - inv.taxable_value - tax)
    inv.status = InvoiceStatus.issued
    inv.confirmed_by_user_id = user.id if user else None
    inv.confirmed_at = datetime.now(UTC)
    db.flush()
    _post_invoice(db, inv)
    audit.record(db, user, "invoice", inv.id, "create", new={"number": inv.number, "total": str(inv.total),
                                                            "source": inv.source.value}, tenant_id=inv.tenant_id)
    if send and tenant.invoice_auto_send and inv.pdf_path:
        send_invoice(db, inv)
    return inv


def cancel_invoice(db: Session, user: User, inv: Invoice, reason: str) -> Invoice:
    if inv.status == InvoiceStatus.cancelled:
        raise InvoiceError("This invoice is already cancelled", "invalid_transition", 409)
    if not reason or not reason.strip():
        raise InvoiceError("Add a reason for cancelling")
    if inv.amount_paid and inv.amount_paid > 0:
        raise InvoiceError("Payments or credit notes are already adjusted against this invoice, so it can't be "
                           "cancelled. Issue a credit note for the rest instead.")
    order = db.get(Order, inv.order_id) if inv.order_id else None
    if inv.status == InvoiceStatus.issued and order is not None and order.invoice_id == inv.id:
        if order.status != OrderStatus.invoiced:
            raise InvoiceError(f"The order is already {order_svc.label(order.status).lower()}. "
                               "Issue a credit note instead of cancelling the invoice.", "invalid_transition", 409)
        order_svc.reopen_after_invoice_cancel(db, user, order, inv.number)
    if inv.status == InvoiceStatus.issued:
        _ledger(db, inv.tenant_id, inv.customer_id, today(), LedgerKind.invoice_cancelled, ref_id=inv.id,
                ref_number=inv.number, credit=inv.total, narration=f"Invoice {inv.number} cancelled: {reason.strip()}")
    inv.status = InvoiceStatus.cancelled
    inv.cancel_reason = reason.strip()[:500]
    inv.cancelled_at = datetime.now(UTC)
    audit.record(db, user, "invoice", inv.id, "update", {"status": "issued"}, {"status": "cancelled",
                                                                               "reason": inv.cancel_reason})
    return inv


# ---------------- credit notes ----------------
def create_credit_note(db: Session, user: User, customer: Customer, amount: Decimal, reason: str,
                       invoice: Invoice | None = None, note_date: date | None = None) -> CreditNote:
    """FR-M7-10."""
    if amount <= 0:
        raise InvoiceError("Amount must be more than zero")
    if invoice is not None:
        if invoice.customer_id != customer.id or invoice.status != InvoiceStatus.issued:
            raise InvoiceError("Link the credit note to an issued invoice of this customer")
        credited = db.scalar(select(func.coalesce(func.sum(CreditNote.amount), 0)).where(
            CreditNote.invoice_id == invoice.id, CreditNote.status == CreditNoteStatus.issued))
        if Decimal(credited) + amount > invoice.total:
            raise InvoiceError(f"Credit notes on {invoice.number} can't exceed the invoice total ₹{invoice.total:,.2f}")
    tenant = db.get(Tenant, customer.tenant_id)
    note_date = note_date or today()
    cn = CreditNote(tenant_id=tenant.id, number=next_doc_number(db, tenant, "credit_note", note_date),
                    note_date=note_date, customer_id=customer.id, invoice_id=invoice.id if invoice else None,
                    amount=money(amount), reason=reason.strip()[:500], status=CreditNoteStatus.issued,
                    created_by_user_id=user.id)
    db.add(cn)
    db.flush()
    _ledger(db, tenant.id, customer.id, note_date, LedgerKind.credit_note, ref_id=cn.id, ref_number=cn.number,
            credit=cn.amount, narration=f"Credit note {cn.number}: {cn.reason}"
            + (f" (against {invoice.number})" if invoice else ""))
    if invoice is not None:  # a credit note against an invoice settles that much of it
        invoice.amount_paid = money((invoice.amount_paid or 0) + min(cn.amount, invoice.total - (invoice.amount_paid or 0)))
    audit.record(db, user, "credit_note", cn.id, "create", new={"number": cn.number, "amount": str(cn.amount)})
    return cn


def cancel_credit_note(db: Session, user: User, cn: CreditNote, reason: str) -> CreditNote:
    if cn.status == CreditNoteStatus.cancelled:
        raise InvoiceError("This credit note is already cancelled", "invalid_transition", 409)
    if not reason or not reason.strip():
        raise InvoiceError("Add a reason for cancelling")
    _ledger(db, cn.tenant_id, cn.customer_id, today(), LedgerKind.credit_note_cancelled, ref_id=cn.id,
            ref_number=cn.number, debit=cn.amount, narration=f"Credit note {cn.number} cancelled: {reason.strip()}")
    if cn.invoice_id:
        inv = db.get(Invoice, cn.invoice_id)
        inv.amount_paid = money((inv.amount_paid or 0) - min(cn.amount, inv.amount_paid or 0))
    cn.status = CreditNoteStatus.cancelled
    cn.cancel_reason = reason.strip()[:500]
    cn.cancelled_at = datetime.now(UTC)
    audit.record(db, user, "credit_note", cn.id, "update", {"status": "issued"}, {"status": "cancelled"})
    return cn


# ---------------- WhatsApp requests (FR-M7-09, FR-M7-11) ----------------
MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov",
                                      "dec"], start=1)}


def period_from_text(text: str | None, tenant: Tenant) -> tuple[date, date, str]:
    """'statement for September' -> that month (most recent one); otherwise this financial year to date."""
    t = (text or "").lower()
    now = today()
    for m in re.findall(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\b", t):
        month = MONTHS[m]
        year = now.year if month <= now.month else now.year - 1
        start = date(year, month, 1)
        end = (date(year + (month == 12), month % 12 + 1, 1) - timedelta(days=1))
        return start, min(end, now), start.strftime("%B %Y")
    start = fy_start(tenant, now)
    return start, now, f"{start:%d %b %Y} to {now:%d %b %Y}"


def send_statement(db: Session, customer: Customer, date_from: date, date_to: date, label: str):
    tenant = db.get(Tenant, customer.tenant_id)
    st = statement(db, customer, date_from, date_to)
    content = pdfs.ledger_pdf(tenant, customer, st)
    closing = st["closing"]
    caption = (f"Statement of account for {label}. Balance "
               + (f"due: ₹{closing:,.2f}." if closing >= 0 else f"in your favour: ₹{abs(closing):,.2f}."))
    return send_document(db, customer, content, f"Statement-{date_from:%Y%m%d}-{date_to:%Y%m%d}.pdf", caption)


def find_customer_invoice(db: Session, customer: Customer, reference: str | None) -> Invoice | None:
    q = select(Invoice).where(Invoice.tenant_id == customer.tenant_id, Invoice.customer_id == customer.id,
                              Invoice.status == InvoiceStatus.issued)
    if reference and norm_number(reference):
        ref = norm_number(reference)
        for inv in db.scalars(q.order_by(Invoice.invoice_date.desc())):
            n = norm_number(inv.number)
            if n == ref or n.endswith(ref) and len(ref) >= 3:
                return inv
        return None
    return db.scalar(q.order_by(Invoice.invoice_date.desc(), Invoice.created_at.desc()).limit(1))
