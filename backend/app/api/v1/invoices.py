"""Invoices, credit notes and customer ledger (M7)."""

import uuid
from datetime import UTC, date, datetime
from types import SimpleNamespace
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Depends, File, HTTPException, Query, Response, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.api.v1.customers import get_customer_or_404
from app.core.database import get_db
from app.core.deps import ALL_STAFF, require_roles
from app.core.validators import normalize_phone
from app.models import (
    AiLog,
    CreditNote,
    Customer,
    Invoice,
    InvoiceSource,
    InvoiceStatus,
    Order,
    Role,
    Tenant,
    User,
)
from app.schemas.common import ImportResult, ImportRowError, Page
from app.schemas.invoices import (
    AttachResult,
    CancelIn,
    CreditNoteIn,
    CreditNoteOut,
    InvoiceDetail,
    InvoiceLineOut,
    InvoiceSummary,
    ReviewIn,
    SendIn,
    Statement,
)
from app.services import audit, excel, media, pdfs
from app.services import invoices as svc
from app.services import orders as order_svc
from app.services.ai import invoice_reader, matcher
from app.services.ai.providers import AIError
from app.services.gst import amount_in_words
from app.services.invoices import InvoiceError

router = APIRouter(tags=["Invoices & ledger"])
staff = require_roles(*ALL_STAFF)
accounts = require_roles(Role.owner, Role.admin, Role.accounts)  # "Invoices and ledger: Full"

MAX_PDF_BYTES = 10 * 1024 * 1024
MAX_UPLOAD_FILES = 50


def _err(exc: InvoiceError) -> HTTPException:
    return HTTPException(exc.http, exc.message)


def scoped(stmt, user: User):
    stmt = stmt.where(Invoice.tenant_id == user.tenant_id)
    if user.role == Role.sales:  # "View own"
        stmt = stmt.where(Invoice.customer_id.in_(
            select(Customer.id).where(Customer.tenant_id == user.tenant_id, Customer.assigned_user_id == user.id)))
    return stmt


def get_invoice_or_404(db: Session, user: User, invoice_id: uuid.UUID, lock: bool = False) -> Invoice:
    stmt = scoped(select(Invoice).where(Invoice.id == invoice_id), user)
    if lock:
        stmt = stmt.with_for_update()
    inv = db.scalar(stmt)
    if inv is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Invoice not found")
    return inv


def _summary(inv: Invoice, customers: dict, orders: dict) -> dict:
    bal = inv.total - (inv.amount_paid or 0) if inv.status == InvoiceStatus.issued else Decimal(0)
    today = svc.today()
    return dict(
        id=inv.id, number=inv.number, invoice_date=inv.invoice_date, due_date=inv.due_date,
        customer_id=inv.customer_id, customer_name=customers.get(inv.customer_id), order_id=inv.order_id,
        order_number=orders.get(inv.order_id), source=inv.source, status=inv.status, total=inv.total,
        amount_paid=inv.amount_paid or Decimal(0), balance_due=bal,
        overdue=bool(inv.status == InvoiceStatus.issued and bal > 0 and inv.due_date and inv.due_date < today),
        has_pdf=bool(inv.pdf_path), pdf_name=inv.pdf_name, sent=inv.sent_message_id is not None,
        created_at=inv.created_at,
    )


def _detail(db: Session, user: User, inv: Invoice) -> InvoiceDetail:
    db.refresh(inv)
    customer = db.get(Customer, inv.customer_id) if inv.customer_id else None
    order = db.get(Order, inv.order_id) if inv.order_id else None
    names = {u.id: u.name for u in db.scalars(select(User).where(User.tenant_id == user.tenant_id))}
    cns = db.scalars(select(CreditNote).where(CreditNote.invoice_id == inv.id).order_by(CreditNote.created_at)).all()
    base = _summary(inv, {customer.id: customer.business_name} if customer else {},
                    {order.id: order.number} if order else {})
    return InvoiceDetail(
        **base, seller_gstin=inv.seller_gstin, buyer_gstin=inv.buyer_gstin, place_of_supply=inv.place_of_supply,
        inter_state=inv.inter_state, taxable_value=inv.taxable_value, cgst=inv.cgst, sgst=inv.sgst, igst=inv.igst,
        tax_total=inv.tax_total, round_off=inv.round_off, notes=inv.notes, ai_extracted=inv.ai_extracted,
        send_note=inv.send_note, sent_message_id=inv.sent_message_id, cancel_reason=inv.cancel_reason,
        cancelled_at=inv.cancelled_at, confirmed_at=inv.confirmed_at,
        confirmed_by_name=names.get(inv.confirmed_by_user_id), created_by_name=names.get(inv.created_by_user_id),
        customer_whatsapp=customer.whatsapp_number if customer else None,
        lines=[InvoiceLineOut.model_validate(l) for l in inv.lines],
        credit_notes=[CreditNoteOut.model_validate(c) for c in cns],
        amount_in_words=amount_in_words(inv.total) if inv.total else "",
        reminders_paused=inv.reminders_paused,
    )


def _filtered(user: User, *, status_: list[InvoiceStatus] | None, customer_id, source, q, date_from, date_to,
              overdue: bool | None):
    stmt = scoped(select(Invoice).outerjoin(Customer, Customer.id == Invoice.customer_id), user)
    if status_:
        stmt = stmt.where(Invoice.status.in_(status_))
    if customer_id:
        stmt = stmt.where(Invoice.customer_id == customer_id)
    if source:
        stmt = stmt.where(Invoice.source == source)
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(Invoice.number.ilike(like), Customer.business_name.ilike(like),
                              Invoice.pdf_name.ilike(like)))
    if date_from:
        stmt = stmt.where(Invoice.invoice_date >= date_from)
    if date_to:
        stmt = stmt.where(Invoice.invoice_date <= date_to)
    if overdue:
        stmt = stmt.where(Invoice.status == InvoiceStatus.issued, Invoice.due_date < svc.today(),
                          Invoice.total > Invoice.amount_paid)
    return stmt


@router.get("/invoices", response_model=Page[InvoiceSummary])
def list_invoices(
    status_filter: list[InvoiceStatus] | None = Query(default=None, alias="status"),
    customer_id: uuid.UUID | None = None, source: InvoiceSource | None = None, q: str | None = None,
    date_from: date | None = None, date_to: date | None = None, overdue: bool | None = None,
    page: int = Query(default=1, ge=1), size: int = Query(default=25, ge=1, le=200),
    db: Session = Depends(get_db), user: User = Depends(staff),
):
    stmt = _filtered(user, status_=status_filter, customer_id=customer_id, source=source, q=q, date_from=date_from,
                     date_to=date_to, overdue=overdue)
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    # invoices waiting for review first, then newest
    rows = db.scalars(stmt.order_by((Invoice.status == InvoiceStatus.review).desc(),
                                    Invoice.invoice_date.desc().nulls_first(), Invoice.created_at.desc())
                      .offset((page - 1) * size).limit(size)).all()
    customers = {c.id: c.business_name for c in db.scalars(select(Customer).where(
        Customer.id.in_({r.customer_id for r in rows if r.customer_id})))} if rows else {}
    orders = {o.id: o.number for o in db.scalars(select(Order).where(
        Order.id.in_({r.order_id for r in rows if r.order_id})))} if rows else {}
    return Page(items=[InvoiceSummary(**_summary(r, customers, orders)) for r in rows], total=total, page=page,
                size=size)


@router.get("/invoices/counts", response_model=dict[str, int])
def invoice_counts(db: Session = Depends(get_db), user: User = Depends(staff)):
    rows = dict(db.execute(scoped(select(Invoice.status, func.count()), user).group_by(Invoice.status)).all())
    overdue = db.scalar(select(func.count()).select_from(_filtered(
        user, status_=None, customer_id=None, source=None, q=None, date_from=None, date_to=None,
        overdue=True).subquery()))
    return {"review": rows.get(InvoiceStatus.review, 0), "issued": rows.get(InvoiceStatus.issued, 0),
            "cancelled": rows.get(InvoiceStatus.cancelled, 0), "overdue": overdue}


# ---------- upload PDFs read by AI (FR-M7-01) ----------
def _match_customer(db: Session, tenant_id: uuid.UUID, fields: dict) -> Customer | None:
    gstin = (fields.get("customer_gstin") or "").strip().upper()
    if gstin:
        c = db.scalar(select(Customer).where(Customer.tenant_id == tenant_id, func.upper(Customer.gstin) == gstin))
        if c:
            return c
    name = fields.get("customer_name")
    if name:
        best, score = None, 0.0
        for c in db.scalars(select(Customer).where(Customer.tenant_id == tenant_id)):
            s = matcher.score(name, SimpleNamespace(sku="", name=c.business_name, aliases=[]))
            if s > score:
                best, score = c, s
        if best is not None and score >= 0.85:
            return best
    return None


@router.post("/invoices/upload", response_model=list[InvoiceSummary])
async def upload_invoices(files: list[UploadFile] = File(...), db: Session = Depends(get_db),
                          user: User = Depends(accounts)):
    """Each PDF becomes an invoice in Review with the fields the AI read. Nothing is shared until confirmed."""
    if len(files) > MAX_UPLOAD_FILES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Upload at most {MAX_UPLOAD_FILES} files at a time")
    tenant = db.get(Tenant, user.tenant_id)
    created: list[Invoice] = []
    for f in files:
        content = await f.read()
        if len(content) > MAX_PDF_BYTES or not content.startswith(b"%PDF"):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"{f.filename}: upload PDF files up to 10 MB")
        inv = Invoice(tenant_id=tenant.id, source=InvoiceSource.uploaded, status=InvoiceStatus.review,
                      created_by_user_id=user.id, pdf_name=(f.filename or "invoice.pdf")[:200], lines=[])
        inv.pdf_path = media.save(tenant.id, content, "application/pdf", inv.pdf_name)
        started = datetime.now(UTC)
        try:
            fields, res, provider = invoice_reader.read_invoice(content, tenant.gstin)
            error = None
        except AIError as exc:
            fields, res, provider, error = {}, None, "mock", str(exc)
        customer = _match_customer(db, tenant.id, fields) if fields else None
        suggestion = {
            "invoice_number": fields.get("invoice_number"),
            "invoice_date": (d.isoformat() if (d := invoice_reader.parse_date(fields.get("invoice_date"))) else None),
            "due_date": (d.isoformat() if (d := invoice_reader.parse_date(fields.get("due_date"))) else None),
            "customer_name": fields.get("customer_name"), "customer_gstin": fields.get("customer_gstin"),
            "customer_id": str(customer.id) if customer else None,
            **{k: (str(a) if (a := invoice_reader.parse_amount(fields.get(k))) is not None else None)
               for k in ("taxable_value", "tax_amount", "total")},
            "error": error,
        }
        inv.ai_extracted = suggestion
        inv.customer_id = customer.id if customer else None
        db.add(inv)
        db.flush()
        db.add(AiLog(tenant_id=tenant.id, kind="invoice_read", provider=res.provider if res else provider,
                     model=res.model if res else "rules-v1", intent="invoice", input_text=inv.pdf_name,
                     input_media="application/pdf", output=suggestion,
                     input_tokens=res.input_tokens if res else 0, output_tokens=res.output_tokens if res else 0,
                     cost_usd=res.cost_usd if res else 0,
                     latency_ms=res.latency_ms if res else int((datetime.now(UTC) - started).total_seconds() * 1000),
                     error=error))
        created.append(inv)
    db.commit()
    return [InvoiceSummary(**_summary(i, {}, {})) for i in created]


@router.post("/invoices/{invoice_id}/confirm", response_model=InvoiceDetail)
def confirm_invoice(invoice_id: uuid.UUID, body: ReviewIn, db: Session = Depends(get_db),
                    user: User = Depends(accounts)):
    inv = get_invoice_or_404(db, user, invoice_id, lock=True)
    if inv.status != InvoiceStatus.review:
        raise HTTPException(status.HTTP_409_CONFLICT, "Only invoices in review can be confirmed")
    customer = get_customer_or_404(db, user, body.customer_id)
    try:
        svc.issue_manual(db, user, inv, customer=customer, number=body.invoice_number, invoice_date=body.invoice_date,
                         due_date=body.due_date, taxable=body.taxable_value, tax=body.tax_amount, total=body.total,
                         send=body.send_to_buyer)
    except InvoiceError as exc:
        db.rollback()
        raise _err(exc) from None
    order_svc.commit_and_send(db)
    return _detail(db, user, inv)


@router.delete("/invoices/{invoice_id}", status_code=status.HTTP_204_NO_CONTENT)
def discard_review(invoice_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(accounts)):
    """Discard an uploaded PDF still in review (wrong file, duplicate). Saved invoices can only be cancelled."""
    inv = get_invoice_or_404(db, user, invoice_id)
    if inv.status != InvoiceStatus.review:
        raise HTTPException(status.HTTP_409_CONFLICT, "A saved invoice can't be deleted; cancel it with a reason")
    audit.record(db, user, "invoice", inv.id, "delete", old={"pdf": inv.pdf_name})
    db.delete(inv)
    db.commit()


# ---------- Excel import (FR-M7-02) ----------
IMPORT_COLUMNS = [
    ("invoice_number", True, "Invoice number exactly as on the invoice"),
    ("invoice_date", True, "Invoice date, e.g. 15-10-2026"),
    ("customer_whatsapp", False, "Customer's WhatsApp number (or give customer_gstin)"),
    ("customer_gstin", False, "Customer's GSTIN (or give customer_whatsapp)"),
    ("taxable_value", False, "Taxable value in Rs (blank = total)"),
    ("tax_amount", False, "Total GST in Rs (CGST + SGST + IGST)"),
    ("total", True, "Invoice total in Rs"),
    ("due_date", False, "Due date; blank = invoice date + customer's credit days"),
]


@router.get("/invoices/import/template")
def invoice_import_template(_: User = Depends(accounts)):
    content = excel.build_template(IMPORT_COLUMNS, ["TI/2026/1234", "15-10-2026", "9876543210", None, 10000, 500,
                                                    10500, None])
    return Response(content, media_type=excel.XLSX_MIME,
                    headers={"Content-Disposition": 'attachment; filename="invoice_import_template.xlsx"'})


@router.post("/invoices/import", response_model=ImportResult)
async def import_invoices(file: UploadFile = File(...), db: Session = Depends(get_db), user: User = Depends(accounts)):
    """Rows are saved as issued invoices (no WhatsApp sending). Attach PDFs afterwards by file name."""
    try:
        rows = excel.read_rows(await file.read(), ["invoice_number", "invoice_date", "total"])
    except excel.ImportFileError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from None
    tid = user.tenant_id
    by_phone = {c.whatsapp_number: c for c in db.scalars(select(Customer).where(Customer.tenant_id == tid))}
    by_gstin = {c.gstin.upper(): c for c in by_phone.values() if c.gstin}
    seen: dict[str, int] = {}
    errors: list[ImportRowError] = []
    created = 0
    for row_no, r in rows:
        errs: list[str] = []
        number = excel.as_text(r.get("invoice_number")) or ""
        inv_date = invoice_reader.parse_date(r.get("invoice_date"))
        due = invoice_reader.parse_date(r.get("due_date")) if r.get("due_date") else None
        total = invoice_reader.parse_amount(r.get("total"))
        taxable = invoice_reader.parse_amount(r.get("taxable_value"))
        tax = invoice_reader.parse_amount(r.get("tax_amount")) or Decimal(0)
        if not number:
            errs.append("invoice_number: required")
        elif svc.number_taken(db, tid, number) or number.upper() in seen:
            errs.append(f"invoice_number: {number} already exists")
        if inv_date is None:
            errs.append("invoice_date: use a date like 15-10-2026")
        if r.get("due_date") and due is None:
            errs.append("due_date: not a valid date")
        if total is None or total <= 0:
            errs.append("total: must be a number above zero")
        customer = None
        phone = excel.as_text(r.get("customer_whatsapp"))
        gstin = (excel.as_text(r.get("customer_gstin")) or "").upper()
        if phone:
            try:
                customer = by_phone.get(normalize_phone(phone))
            except ValueError:
                pass
        if customer is None and gstin:
            customer = by_gstin.get(gstin)
        if customer is None:
            errs.append("customer: no customer with that WhatsApp number or GSTIN")
        if errs:
            errors.append(ImportRowError(row=row_no, errors=errs))
            continue
        inv = Invoice(tenant_id=tid, source=InvoiceSource.excel, status=InvoiceStatus.review,
                      created_by_user_id=user.id, lines=[])
        db.add(inv)
        try:
            with db.begin_nested():
                svc.issue_manual(db, user, inv, customer=customer, number=number, invoice_date=inv_date, due_date=due,
                                 taxable=taxable if taxable is not None else total - tax, tax=tax, total=total,
                                 send=False)
            created += 1
            seen[number.upper()] = row_no
        except InvoiceError as exc:
            errors.append(ImportRowError(row=row_no, errors=[exc.message]))
    db.commit()
    return ImportResult(total_rows=len(rows), created=created, updated=0, failed=len(errors), errors=errors)


@router.post("/invoices/attach-pdfs", response_model=AttachResult)
async def attach_pdfs(files: list[UploadFile] = File(...), db: Session = Depends(get_db),
                      user: User = Depends(accounts)):
    """Match PDFs to saved invoices by file name = invoice number (e.g. TI-2026-1234.pdf -> TI/2026/1234)."""
    invoices = {svc.norm_number(i.number): i for i in db.scalars(select(Invoice).where(
        Invoice.tenant_id == user.tenant_id, Invoice.status == InvoiceStatus.issued))}
    matched, unmatched = [], []
    for f in files[:MAX_UPLOAD_FILES * 4]:
        content = await f.read()
        stem = (f.filename or "").rsplit(".", 1)[0]
        inv = invoices.get(svc.norm_number(stem))
        if inv is None or not content.startswith(b"%PDF") or len(content) > MAX_PDF_BYTES:
            unmatched.append(f.filename or "?")
            continue
        inv.pdf_path = media.save(user.tenant_id, content, "application/pdf", f.filename)
        inv.pdf_name = f.filename
        matched.append(inv.number)
    db.commit()
    return AttachResult(matched=matched, unmatched=unmatched)


@router.get("/invoices/export")
def export_invoices(
    status_filter: list[InvoiceStatus] | None = Query(default=None, alias="status"),
    customer_id: uuid.UUID | None = None, source: InvoiceSource | None = None, q: str | None = None,
    date_from: date | None = None, date_to: date | None = None, overdue: bool | None = None,
    db: Session = Depends(get_db), user: User = Depends(staff),
):
    stmt = _filtered(user, status_=status_filter, customer_id=customer_id, source=source, q=q, date_from=date_from,
                     date_to=date_to, overdue=overdue)
    rows = db.scalars(stmt.order_by(Invoice.invoice_date.desc().nulls_first())).all()
    customers = {c.id: c for c in db.scalars(select(Customer).where(Customer.tenant_id == user.tenant_id))}
    headers = ["invoice_number", "invoice_date", "due_date", "customer", "customer_gstin", "status", "source",
               "taxable_value", "cgst", "sgst", "igst", "total", "paid", "balance"]
    data = []
    for i in rows:
        c = customers.get(i.customer_id)
        data.append([i.number, i.invoice_date, i.due_date, c.business_name if c else None, i.buyer_gstin,
                     i.status.value, i.source.value, float(i.taxable_value), float(i.cgst), float(i.sgst),
                     float(i.igst), float(i.total), float(i.amount_paid or 0),
                     float(i.total - (i.amount_paid or 0)) if i.status == InvoiceStatus.issued else 0])
    return Response(excel.export_rows(headers, data, "Invoices"), media_type=excel.XLSX_MIME,
                    headers={"Content-Disposition": 'attachment; filename="invoices.xlsx"'})


# ---------- single invoice ----------
@router.get("/invoices/{invoice_id}", response_model=InvoiceDetail)
def get_invoice(invoice_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(staff)):
    return _detail(db, user, get_invoice_or_404(db, user, invoice_id))


@router.get("/invoices/{invoice_id}/pdf")
def invoice_pdf(invoice_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(staff)):
    inv = get_invoice_or_404(db, user, invoice_id)
    if not inv.pdf_path:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No PDF for this invoice")
    return FileResponse(media.full_path(inv.pdf_path), media_type="application/pdf",
                        filename=inv.pdf_name or "invoice.pdf")


@router.post("/invoices/{invoice_id}/pdf", response_model=InvoiceDetail)
async def attach_pdf(invoice_id: uuid.UUID, file: UploadFile = File(...), db: Session = Depends(get_db),
                     user: User = Depends(accounts)):
    inv = get_invoice_or_404(db, user, invoice_id, lock=True)
    if inv.status != InvoiceStatus.issued:
        raise HTTPException(status.HTTP_409_CONFLICT, "Attach PDFs to saved invoices only")
    content = await file.read()
    if not content.startswith(b"%PDF") or len(content) > MAX_PDF_BYTES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Upload a PDF up to 10 MB")
    inv.pdf_name = file.filename or f"{inv.number}.pdf"
    inv.pdf_path = media.save(user.tenant_id, content, "application/pdf", inv.pdf_name)
    db.commit()
    return _detail(db, user, inv)


@router.post("/invoices/{invoice_id}/send", response_model=InvoiceDetail)
def send_invoice(invoice_id: uuid.UUID, body: SendIn, db: Session = Depends(get_db), user: User = Depends(accounts)):
    """FR-M7-05 manual (re)send of the PDF to the buyer on WhatsApp."""
    inv = get_invoice_or_404(db, user, invoice_id)
    try:
        msg, note = svc.send_invoice(db, inv, body.caption)
    except InvoiceError as exc:
        raise _err(exc) from None
    order_svc.commit_and_send(db)
    if msg is None:
        raise HTTPException(status.HTTP_409_CONFLICT, note)
    return _detail(db, user, inv)


@router.post("/invoices/{invoice_id}/cancel", response_model=InvoiceDetail)
def cancel_invoice(invoice_id: uuid.UUID, body: CancelIn, db: Session = Depends(get_db),
                   user: User = Depends(accounts)):
    inv = get_invoice_or_404(db, user, invoice_id, lock=True)
    if inv.status == InvoiceStatus.review:
        raise HTTPException(status.HTTP_409_CONFLICT, "Discard an invoice in review instead of cancelling it")
    try:
        svc.cancel_invoice(db, user, inv, body.reason)
    except (InvoiceError, order_svc.OrderError) as exc:
        db.rollback()
        raise HTTPException(exc.http, exc.message) from None
    order_svc.commit_and_send(db)
    return _detail(db, user, inv)


# ---------- credit notes (FR-M7-10) ----------
def _cn_out(db: Session, cns: list[CreditNote]) -> list[CreditNoteOut]:
    if not cns:
        return []
    customers = {c.id: c.business_name for c in db.scalars(select(Customer).where(
        Customer.id.in_({c.customer_id for c in cns})))}
    invs = {i.id: i.number for i in db.scalars(select(Invoice).where(
        Invoice.id.in_({c.invoice_id for c in cns if c.invoice_id})))}
    return [CreditNoteOut.model_validate(c).model_copy(update={
        "customer_name": customers.get(c.customer_id), "invoice_number": invs.get(c.invoice_id)}) for c in cns]


@router.get("/credit-notes", response_model=list[CreditNoteOut])
def list_credit_notes(customer_id: uuid.UUID | None = None, db: Session = Depends(get_db),
                      user: User = Depends(staff)):
    stmt = select(CreditNote).where(CreditNote.tenant_id == user.tenant_id)
    if customer_id:
        stmt = stmt.where(CreditNote.customer_id == customer_id)
    if user.role == Role.sales:
        stmt = stmt.where(CreditNote.customer_id.in_(select(Customer.id).where(
            Customer.tenant_id == user.tenant_id, Customer.assigned_user_id == user.id)))
    return _cn_out(db, list(db.scalars(stmt.order_by(CreditNote.note_date.desc(), CreditNote.created_at.desc())
                                         .limit(500))))


@router.post("/credit-notes", response_model=CreditNoteOut, status_code=status.HTTP_201_CREATED)
def create_credit_note(body: CreditNoteIn, db: Session = Depends(get_db), user: User = Depends(accounts)):
    customer = get_customer_or_404(db, user, body.customer_id)
    invoice = get_invoice_or_404(db, user, body.invoice_id) if body.invoice_id else None
    try:
        cn = svc.create_credit_note(db, user, customer, body.amount, body.reason, invoice, body.note_date)
    except InvoiceError as exc:
        db.rollback()
        raise _err(exc) from None
    db.commit()
    return _cn_out(db, [cn])[0]


@router.post("/credit-notes/{cn_id}/cancel", response_model=CreditNoteOut)
def cancel_credit_note(cn_id: uuid.UUID, body: CancelIn, db: Session = Depends(get_db),
                       user: User = Depends(accounts)):
    cn = db.scalar(select(CreditNote).where(CreditNote.id == cn_id, CreditNote.tenant_id == user.tenant_id)
                   .with_for_update())
    if cn is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Credit note not found")
    try:
        svc.cancel_credit_note(db, user, cn, body.reason)
    except InvoiceError as exc:
        db.rollback()
        raise _err(exc) from None
    db.commit()
    return _cn_out(db, [cn])[0]


# ---------- ledger (FR-M7-07, 08, 09) ----------
def _statement(db: Session, user: User, customer_id: uuid.UUID, date_from: date | None, date_to: date | None):
    customer = get_customer_or_404(db, user, customer_id)
    if date_from and date_to and date_from > date_to:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "From date is after To date")
    return customer, svc.statement(db, customer, date_from, date_to)


@router.get("/customers/{customer_id}/ledger", response_model=Statement)
def customer_ledger(customer_id: uuid.UUID, date_from: date | None = None, date_to: date | None = None,
                    db: Session = Depends(get_db), user: User = Depends(staff)):
    customer, st = _statement(db, user, customer_id, date_from, date_to)
    return Statement(**st, customer_name=customer.business_name, opening_balance_master=customer.opening_balance)


@router.get("/customers/{customer_id}/ledger.pdf")
def customer_ledger_pdf(customer_id: uuid.UUID, date_from: date | None = None, date_to: date | None = None,
                        db: Session = Depends(get_db), user: User = Depends(staff)):
    customer, st = _statement(db, user, customer_id, date_from, date_to)
    content = pdfs.ledger_pdf(db.get(Tenant, user.tenant_id), customer, st)
    name = f"Statement-{customer.business_name[:30].replace(' ', '-')}-{st['date_to']:%Y%m%d}.pdf"
    return Response(content, media_type="application/pdf", headers={"Content-Disposition": f'attachment; filename="{name}"'})


@router.post("/customers/{customer_id}/ledger/send")
def send_ledger(customer_id: uuid.UUID, date_from: date | None = None, date_to: date | None = None,
                db: Session = Depends(get_db), user: User = Depends(require_roles(Role.owner, Role.admin, Role.sales,
                                                                                    Role.accounts))):
    customer, st = _statement(db, user, customer_id, date_from, date_to)
    msg, note = svc.send_statement(db, customer, st["date_from"], st["date_to"],
                                   f"{st['date_from']:%d %b %Y} to {st['date_to']:%d %b %Y}")
    order_svc.commit_and_send(db)
    if msg is None:
        raise HTTPException(status.HTTP_409_CONFLICT, note)
    return {"ok": True, "message_id": str(msg.id)}


OB_COLUMNS = [
    ("customer_whatsapp", False, "Customer's WhatsApp number (or give customer_gstin)"),
    ("customer_gstin", False, "Customer's GSTIN (or give customer_whatsapp)"),
    ("opening_balance", True, "What the customer owes you at go-live, in Rs. Negative = advance / in their favour"),
]


@router.get("/ledger/opening-balances/template")
def opening_template(_: User = Depends(accounts)):
    content = excel.build_template(OB_COLUMNS, ["9876543210", None, 25000])
    return Response(content, media_type=excel.XLSX_MIME,
                    headers={"Content-Disposition": 'attachment; filename="opening_balances_template.xlsx"'})


@router.post("/ledger/opening-balances/import", response_model=ImportResult)
async def import_opening_balances(file: UploadFile = File(...), db: Session = Depends(get_db),
                                  user: User = Depends(accounts)):
    """FR-M7-08: set each customer's opening balance from Excel at go-live."""
    try:
        rows = excel.read_rows(await file.read(), ["opening_balance"])
    except excel.ImportFileError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from None
    tid = user.tenant_id
    customers = list(db.scalars(select(Customer).where(Customer.tenant_id == tid)))
    by_phone = {c.whatsapp_number: c for c in customers}
    by_gstin = {c.gstin.upper(): c for c in customers if c.gstin}
    errors: list[ImportRowError] = []
    updated = 0
    for row_no, r in rows:
        customer = None
        phone = excel.as_text(r.get("customer_whatsapp"))
        gstin = (excel.as_text(r.get("customer_gstin")) or "").upper()
        if phone:
            try:
                customer = by_phone.get(normalize_phone(phone))
            except ValueError:
                pass
        if customer is None and gstin:
            customer = by_gstin.get(gstin)
        try:
            amount = Decimal(str(r.get("opening_balance")).replace(",", ""))
        except (InvalidOperation, ValueError):
            amount = None
        errs = []
        if customer is None:
            errs.append("customer: no customer with that WhatsApp number or GSTIN")
        if amount is None:
            errs.append("opening_balance: must be a number")
        if errs:
            errors.append(ImportRowError(row=row_no, errors=errs))
            continue
        old = {"opening_balance": str(customer.opening_balance)}
        customer.opening_balance = amount.quantize(Decimal("0.01"))
        audit.record(db, user, "customer", customer.id, "update", old, {"opening_balance": str(customer.opening_balance)})
        updated += 1
    db.commit()
    return ImportResult(total_rows=len(rows), created=0, updated=updated, failed=len(errors), errors=errors)


@router.get("/ledger/balances")
def balances(q: str | None = None, only_due: bool = False, db: Session = Depends(get_db), user: User = Depends(staff)):
    """Every customer's current balance (receivables). Detailed ageing comes with M8."""
    from app.api.v1.customers import scoped as cust_scoped
    from app.models import LedgerEntry

    stmt = cust_scoped(select(Customer), user)
    if q:
        stmt = stmt.where(or_(Customer.business_name.ilike(f"%{q}%"), Customer.whatsapp_number.ilike(f"%{q}%")))
    customers = list(db.scalars(stmt.order_by(Customer.business_name)))
    sums = dict(db.execute(select(LedgerEntry.customer_id, func.sum(LedgerEntry.debit - LedgerEntry.credit))
                           .where(LedgerEntry.tenant_id == user.tenant_id).group_by(LedgerEntry.customer_id)).all())
    today = svc.today()
    overdue = dict(db.execute(select(Invoice.customer_id, func.sum(Invoice.total - Invoice.amount_paid)).where(
        Invoice.tenant_id == user.tenant_id, Invoice.status == InvoiceStatus.issued, Invoice.due_date < today,
        Invoice.total > Invoice.amount_paid).group_by(Invoice.customer_id)).all())
    out = []
    for c in customers:
        bal = Decimal(c.opening_balance or 0) + Decimal(sums.get(c.id) or 0)
        if only_due and bal <= 0:
            continue
        out.append({"customer_id": str(c.id), "customer_name": c.business_name, "whatsapp_number": c.whatsapp_number,
                    "opening_balance": str(c.opening_balance), "balance": str(bal.quantize(Decimal("0.01"))),
                    "overdue": str(Decimal(overdue.get(c.id) or 0).quantize(Decimal("0.01"))),
                    "credit_limit": str(c.credit_limit)})
    return out
