"""Collections API (M8): payments, payment links, reminders, ageing, gateway settings."""

import uuid
from datetime import date, datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.api.v1.customers import get_customer_or_404
from app.api.v1.customers import scoped as customers_scoped
from app.core.config import settings
from app.core.crypto import decrypt, encrypt, mask
from app.core.database import get_db
from app.core.deps import ALL_STAFF, MANAGERS, require_roles
from app.models import (
    Customer,
    GatewayMode,
    Invoice,
    LinkStatus,
    Payment,
    PaymentAllocation,
    PaymentGatewayAccount,
    PaymentLink,
    PaymentMode,
    PaymentSource,
    PaymentStatus,
    ReminderLog,
    Role,
    Tenant,
    User,
)
from app.schemas.common import Page
from app.services import audit, excel
from app.services import orders as order_svc
from app.services import payments as svc
from app.services.gateways import GatewayError, RazorpayGateway
from app.services.payments import PaymentError
from app.services.reminders import run_reminders, step_label

router = APIRouter(tags=["Collections"])
staff = require_roles(*ALL_STAFF)
finance = require_roles(Role.owner, Role.accounts)              # "Payments and reconciliation: Full"
recorders = require_roles(Role.owner, Role.accounts, Role.sales)  # Sales: cash or cheque for own customers
collectors = require_roles(Role.owner, Role.admin, Role.accounts)
managers = require_roles(*MANAGERS)


def _err(exc: PaymentError) -> HTTPException:
    return HTTPException(exc.http, exc.message)


# ---------------- schemas ----------------
class AllocationOut(BaseModel):
    invoice_id: uuid.UUID
    invoice_number: str | None
    amount: Decimal


class PaymentOut(BaseModel):
    id: uuid.UUID
    number: str
    customer_id: uuid.UUID | None
    customer_name: str | None
    amount: Decimal
    payment_date: date
    mode: PaymentMode
    status: PaymentStatus
    source: PaymentSource
    reference: str | None
    cheque_bank: str | None
    cheque_date: date | None
    payer_name: str | None
    notes: str | None
    unallocated: Decimal
    status_reason: str | None
    receipt_sent: bool
    receipt_note: str | None
    message_id: uuid.UUID | None
    has_proof_media: bool
    ai_extracted: dict | None
    duplicate_of: str | None = None
    allocations: list[AllocationOut]
    created_at: datetime


class PaymentIn(BaseModel):
    customer_id: uuid.UUID
    amount: Decimal = Field(gt=0, max_digits=14, decimal_places=2)
    mode: PaymentMode
    payment_date: date | None = None
    reference: str | None = Field(default=None, max_length=60)
    cheque_bank: str | None = Field(default=None, max_length=100)
    cheque_date: date | None = None
    notes: str | None = Field(default=None, max_length=500)
    invoice_ids: list[uuid.UUID] | None = None


class ConfirmIn(BaseModel):
    customer_id: uuid.UUID
    amount: Decimal = Field(gt=0)
    mode: PaymentMode
    payment_date: date
    reference: str | None = None
    cheque_bank: str | None = None
    invoice_ids: list[uuid.UUID] | None = None


class ReasonIn(BaseModel):
    reason: str = Field(min_length=2, max_length=300)


class LinkIn(BaseModel):
    customer_id: uuid.UUID
    amount: Decimal | None = Field(default=None, gt=0)
    invoice_ids: list[uuid.UUID] | None = None
    send: bool = True


class LinkOut(BaseModel):
    id: uuid.UUID
    customer_id: uuid.UUID
    customer_name: str | None
    amount: Decimal
    invoice_ids: list | None
    gateway: GatewayMode
    short_url: str | None
    status: LinkStatus
    purpose: str
    expires_at: datetime | None
    paid_at: datetime | None
    created_at: datetime
    send_note: str | None = None


class GatewayIn(BaseModel):
    mode: GatewayMode
    key_id: str | None = Field(default=None, max_length=64)
    key_secret: str | None = Field(default=None, max_length=200)
    webhook_secret: str | None = Field(default=None, max_length=200)


class GatewayOut(BaseModel):
    mode: GatewayMode
    key_id: str | None
    key_secret_masked: str | None
    webhook_secret_masked: str | None
    webhook_url_path: str
    simulator_enabled: bool


class PauseIn(BaseModel):
    paused: bool


# ---------------- helpers ----------------
def _scope(stmt, user: User, col):
    """Sales see only their own customers' records (callers add the tenant filter)."""
    if user.role == Role.sales:
        stmt = stmt.where(col.in_(select(Customer.id).where(Customer.tenant_id == user.tenant_id,
                                                            Customer.assigned_user_id == user.id)))
    return stmt


def _payment_or_404(db: Session, user: User, pid: uuid.UUID, lock: bool = False) -> Payment:
    stmt = _scope(select(Payment).where(Payment.id == pid, Payment.tenant_id == user.tenant_id), user,
                  Payment.customer_id)
    if lock:
        stmt = stmt.with_for_update()
    p = db.scalar(stmt)
    if p is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Payment not found")
    return p


def _out(db: Session, rows: list[Payment]) -> list[PaymentOut]:
    if not rows:
        return []
    names = {c.id: c.business_name for c in db.scalars(select(Customer).where(
        Customer.id.in_({r.customer_id for r in rows if r.customer_id})))}
    inv_ids = {a.invoice_id for r in rows for a in r.allocations}
    invs = {i.id: i.number for i in db.scalars(select(Invoice).where(Invoice.id.in_(inv_ids)))} if inv_ids else {}
    from app.models import Message

    media = {m.id for m in db.scalars(select(Message).where(
        Message.id.in_({r.message_id for r in rows if r.message_id}), Message.media_path.is_not(None)))}
    out = []
    for r in rows:
        dup = svc.find_duplicate(db, r.tenant_id, r.reference, exclude=r.id) if r.status == PaymentStatus.proposed else None
        out.append(PaymentOut(
            id=r.id, number=r.number, customer_id=r.customer_id, customer_name=names.get(r.customer_id),
            amount=r.amount, payment_date=r.payment_date, mode=r.mode, status=r.status, source=r.source,
            reference=r.reference, cheque_bank=r.cheque_bank, cheque_date=r.cheque_date, payer_name=r.payer_name,
            notes=r.notes, unallocated=r.unallocated, status_reason=r.status_reason,
            receipt_sent=r.receipt_message_id is not None, receipt_note=r.receipt_note, message_id=r.message_id,
            has_proof_media=r.message_id in media, ai_extracted=r.ai_extracted,
            duplicate_of=dup.number if dup else None,
            allocations=[AllocationOut(invoice_id=a.invoice_id, invoice_number=invs.get(a.invoice_id), amount=a.amount)
                         for a in r.allocations],
            created_at=r.created_at))
    return out


def _link_out(db: Session, links: list[PaymentLink], note: str | None = None) -> list[LinkOut]:
    names = {c.id: c.business_name for c in db.scalars(select(Customer).where(
        Customer.id.in_({link.customer_id for link in links})))} if links else {}
    return [LinkOut(id=lk.id, customer_id=lk.customer_id, customer_name=names.get(lk.customer_id), amount=lk.amount,
                    invoice_ids=lk.invoice_ids, gateway=lk.gateway, short_url=lk.short_url, status=lk.status,
                    purpose=lk.purpose, expires_at=lk.expires_at, paid_at=lk.paid_at, created_at=lk.created_at,
                    send_note=note) for lk in links]


# ---------------- gateway settings ----------------
def _gateway_out(acc: PaymentGatewayAccount | None, tenant_id: uuid.UUID) -> GatewayOut:
    return GatewayOut(mode=acc.mode if acc else GatewayMode.simulator, key_id=acc.key_id if acc else None,
                      key_secret_masked=mask(decrypt(acc.key_secret_enc)) if acc else None,
                      webhook_secret_masked=mask(decrypt(acc.webhook_secret_enc)) if acc else None,
                      webhook_url_path=f"/api/webhooks/razorpay/{tenant_id}", simulator_enabled=settings.ENABLE_SIMULATOR)


@router.get("/payments/gateway", response_model=GatewayOut)
def get_gateway_settings(db: Session = Depends(get_db), user: User = Depends(collectors)):
    return _gateway_out(svc.gateway_account(db, user.tenant_id), user.tenant_id)


@router.put("/payments/gateway", response_model=GatewayOut)
def save_gateway(body: GatewayIn, db: Session = Depends(get_db), user: User = Depends(managers)):
    acc = svc.gateway_account(db, user.tenant_id)
    if acc is None:
        acc = PaymentGatewayAccount(tenant_id=user.tenant_id)
        db.add(acc)
    acc.mode = body.mode
    if body.mode == GatewayMode.razorpay:
        acc.key_id = (body.key_id or acc.key_id or "").strip() or None
        if body.key_secret:
            acc.key_secret_enc = encrypt(body.key_secret.strip())
        if body.webhook_secret:
            acc.webhook_secret_enc = encrypt(body.webhook_secret.strip())
        if not acc.key_id or not acc.key_secret_enc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Enter the Razorpay Key ID and Key Secret")
    acc.is_active = True
    audit.record(db, user, "payment_gateway", user.tenant_id, "update", new={"mode": body.mode.value,
                                                                             "key_id": acc.key_id})
    db.commit()
    return _gateway_out(acc, user.tenant_id)


@router.post("/payments/gateway/test")
def test_gateway(db: Session = Depends(get_db), user: User = Depends(managers)):
    acc = svc.gateway_account(db, user.tenant_id)
    if acc is None or acc.mode == GatewayMode.simulator:
        return {"ok": True, "message": "Simulator mode: links open a test payment page in this app."}
    try:
        RazorpayGateway(acc).test()
    except GatewayError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, exc.message) from None
    return {"ok": True, "message": "Connected to Razorpay."}


# ---------------- payments ----------------
@router.get("/payments", response_model=Page[PaymentOut])
def list_payments(
    status_filter: list[PaymentStatus] | None = Query(default=None, alias="status"),
    customer_id: uuid.UUID | None = None, mode: PaymentMode | None = None, q: str | None = None,
    date_from: date | None = None, date_to: date | None = None,
    page: int = Query(default=1, ge=1), size: int = Query(default=25, ge=1, le=200),
    db: Session = Depends(get_db), user: User = Depends(staff),
):
    stmt = _scope(select(Payment).outerjoin(Customer, Customer.id == Payment.customer_id)
                  .where(Payment.tenant_id == user.tenant_id), user, Payment.customer_id)
    if status_filter:
        stmt = stmt.where(Payment.status.in_(status_filter))
    if customer_id:
        stmt = stmt.where(Payment.customer_id == customer_id)
    if mode:
        stmt = stmt.where(Payment.mode == mode)
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(Payment.number.ilike(like), Payment.reference.ilike(like),
                              Customer.business_name.ilike(like)))
    if date_from:
        stmt = stmt.where(Payment.payment_date >= date_from)
    if date_to:
        stmt = stmt.where(Payment.payment_date <= date_to)
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = list(db.scalars(stmt.order_by(Payment.created_at.desc()).offset((page - 1) * size).limit(size)))
    return Page(items=_out(db, rows), total=total, page=page, size=size)


@router.post("/payments", response_model=PaymentOut, status_code=status.HTTP_201_CREATED)
def record_payment(body: PaymentIn, db: Session = Depends(get_db), user: User = Depends(recorders)):
    """FR-M8-06: offline payments. Cheques stay Pending clearance until cleared."""
    if user.role == Role.sales and body.mode not in (PaymentMode.cash, PaymentMode.cheque):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Sales can record cash or cheque payments only")
    if body.mode == PaymentMode.link:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Link payments are recorded automatically by the gateway")
    customer = get_customer_or_404(db, user, body.customer_id)
    try:
        p = svc.record_payment(db, user, customer, amount=body.amount, mode=body.mode, payment_date=body.payment_date,
                               reference=body.reference, cheque_bank=body.cheque_bank, cheque_date=body.cheque_date,
                               notes=body.notes, invoice_ids=body.invoice_ids)
    except PaymentError as exc:
        db.rollback()
        raise _err(exc) from None
    order_svc.commit_and_send(db)
    db.refresh(p)
    return _out(db, [p])[0]


@router.get("/payments/{payment_id}", response_model=PaymentOut)
def get_payment(payment_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(staff)):
    return _out(db, [_payment_or_404(db, user, payment_id)])[0]


def _act(db: Session, user: User, payment_id: uuid.UUID, fn) -> PaymentOut:
    p = _payment_or_404(db, user, payment_id, lock=True)
    try:
        fn(p)
    except PaymentError as exc:
        db.rollback()
        raise _err(exc) from None
    order_svc.commit_and_send(db)
    db.refresh(p)
    return _out(db, [p])[0]


@router.post("/payments/{payment_id}/confirm", response_model=PaymentOut)
def confirm_proposal(payment_id: uuid.UUID, body: ConfirmIn, db: Session = Depends(get_db),
                     user: User = Depends(require_roles(Role.owner, Role.admin, Role.accounts))):
    """Business rule: an AI-read screenshot becomes a payment only when Accounts, Admin or Owner confirms it."""
    customer = get_customer_or_404(db, user, body.customer_id)
    return _act(db, user, payment_id, lambda p: svc.confirm_proposal(
        db, user, p, customer=customer, amount=body.amount, mode=body.mode, payment_date=body.payment_date,
        reference=body.reference, cheque_bank=body.cheque_bank, invoice_ids=body.invoice_ids))


@router.post("/payments/{payment_id}/reject", response_model=PaymentOut)
def reject_proposal(payment_id: uuid.UUID, body: ReasonIn, db: Session = Depends(get_db),
                    user: User = Depends(require_roles(Role.owner, Role.admin, Role.accounts))):
    return _act(db, user, payment_id, lambda p: svc.reject_proposal(db, user, p, body.reason))


@router.post("/payments/{payment_id}/clear", response_model=PaymentOut)
def clear_cheque(payment_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(finance)):
    return _act(db, user, payment_id, lambda p: svc.clear_cheque(db, user, p))


@router.post("/payments/{payment_id}/bounce", response_model=PaymentOut)
def bounce_cheque(payment_id: uuid.UUID, body: ReasonIn, db: Session = Depends(get_db), user: User = Depends(finance)):
    return _act(db, user, payment_id, lambda p: svc.bounce_cheque(db, user, p, body.reason))


@router.post("/payments/{payment_id}/receipt", response_model=PaymentOut)
def resend_receipt(payment_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(finance)):
    def run(p: Payment):
        if p.status != PaymentStatus.confirmed:
            raise PaymentError("Receipts are sent for confirmed payments only", "invalid_transition", 409)
        svc.send_receipt(db, p)
    return _act(db, user, payment_id, run)


@router.get("/invoices/{invoice_id}/payments", response_model=list[PaymentOut])
def invoice_payments(invoice_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(staff)):
    from app.api.v1.invoices import get_invoice_or_404

    inv = get_invoice_or_404(db, user, invoice_id)
    rows = list(db.scalars(select(Payment).join(PaymentAllocation, PaymentAllocation.payment_id == Payment.id)
                           .where(PaymentAllocation.invoice_id == inv.id).order_by(Payment.payment_date)))
    return _out(db, rows)


# ---------------- payment links ----------------
@router.get("/payment-links", response_model=list[LinkOut])
def list_links(customer_id: uuid.UUID | None = None, status_filter: LinkStatus | None = Query(default=None, alias="status"),
               db: Session = Depends(get_db), user: User = Depends(staff)):
    stmt = _scope(select(PaymentLink).where(PaymentLink.tenant_id == user.tenant_id), user, PaymentLink.customer_id)
    if customer_id:
        stmt = stmt.where(PaymentLink.customer_id == customer_id)
    if status_filter:
        stmt = stmt.where(PaymentLink.status == status_filter)
    return _link_out(db, list(db.scalars(stmt.order_by(PaymentLink.created_at.desc()).limit(200))))


@router.post("/payment-links", response_model=LinkOut, status_code=status.HTTP_201_CREATED)
def create_link(body: LinkIn, db: Session = Depends(get_db), user: User = Depends(collectors)):
    customer = get_customer_or_404(db, user, body.customer_id)
    try:
        link = svc.create_link(db, user, customer, amount=body.amount, invoice_ids=body.invoice_ids)
    except PaymentError as exc:
        db.rollback()
        raise _err(exc) from None
    note = None
    if body.send:
        _, note = svc.send_link(db, link)
    order_svc.commit_and_send(db)
    return _link_out(db, [link], note)[0]


def _link_or_404(db: Session, user: User, link_id: uuid.UUID) -> PaymentLink:
    link = db.scalar(_scope(select(PaymentLink).where(PaymentLink.id == link_id, PaymentLink.tenant_id == user.tenant_id),
                            user, PaymentLink.customer_id).with_for_update())
    if link is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Payment link not found")
    return link


@router.post("/payment-links/{link_id}/send", response_model=LinkOut)
def send_link(link_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(collectors)):
    link = _link_or_404(db, user, link_id)
    if link.status != LinkStatus.created:
        raise HTTPException(status.HTTP_409_CONFLICT, "Only open links can be sent")
    msg, note = svc.send_link(db, link)
    order_svc.commit_and_send(db)
    if msg is None:
        raise HTTPException(status.HTTP_409_CONFLICT, note)
    return _link_out(db, [link])[0]


@router.post("/payment-links/{link_id}/cancel", response_model=LinkOut)
def cancel_link(link_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(collectors)):
    link = _link_or_404(db, user, link_id)
    try:
        svc.cancel_link(db, link)
    except PaymentError as exc:
        db.rollback()
        raise _err(exc) from None
    db.commit()
    return _link_out(db, [link])[0]


@router.post("/payment-links/{link_id}/simulate-paid", response_model=PaymentOut)
def simulate_paid(link_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(collectors)):
    """Simulator: pretend the buyer paid this link (what the gateway webhook does for real links)."""
    link = _link_or_404(db, user, link_id)
    return _sim_pay(db, link)


def _sim_pay(db: Session, link: PaymentLink) -> PaymentOut:
    if not settings.ENABLE_SIMULATOR or link.gateway != GatewayMode.simulator:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Only simulator links can be paid here")
    if link.status != LinkStatus.created:
        raise HTTPException(status.HTTP_409_CONFLICT, f"This link is already {link.status.value}")
    try:
        p = svc.link_paid(db, link, gateway_payment_id=f"pay_sim_{uuid.uuid4().hex[:14]}", method="upi")
    except PaymentError as exc:
        db.rollback()
        raise _err(exc) from None
    order_svc.commit_and_send(db)
    db.refresh(p)
    return _out(db, [p])[0]


class PublicLink(BaseModel):
    company: str
    customer: str
    amount: Decimal
    status: LinkStatus
    simulator: bool


@router.get("/public/pay/{link_id}", response_model=PublicLink)
def public_link(link_id: uuid.UUID, db: Session = Depends(get_db)):
    """Test payment page for simulator links (no login). Real links go to the gateway's own page."""
    link = db.get(PaymentLink, link_id)
    if link is None or link.gateway != GatewayMode.simulator or not settings.ENABLE_SIMULATOR:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Link not found")
    return PublicLink(company=db.get(Tenant, link.tenant_id).name, customer=db.get(Customer, link.customer_id).business_name,
                      amount=link.amount, status=link.status, simulator=True)


@router.post("/public/pay/{link_id}")
def public_pay(link_id: uuid.UUID, db: Session = Depends(get_db)):
    link = db.scalar(select(PaymentLink).where(PaymentLink.id == link_id).with_for_update())
    if link is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Link not found")
    p = _sim_pay(db, link)
    return {"ok": True, "receipt": p.number}


# ---------------- reminders ----------------
@router.post("/collections/reminders/run")
def run_reminders_now(db: Session = Depends(get_db), user: User = Depends(collectors)):
    """Run today's reminder step now (it also runs automatically every morning). Never sends a step twice."""
    summary = run_reminders(db, db.get(Tenant, user.tenant_id))
    order_svc.commit_and_send(db)
    return summary


@router.get("/collections/reminders")
def reminder_log(page: int = Query(default=1, ge=1), size: int = Query(default=50, le=200),
                 db: Session = Depends(get_db), user: User = Depends(staff)):
    stmt = _scope(select(ReminderLog).where(ReminderLog.tenant_id == user.tenant_id), user, ReminderLog.customer_id)
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = list(db.scalars(stmt.order_by(ReminderLog.created_at.desc()).offset((page - 1) * size).limit(size)))
    names = {c.id: c.business_name for c in db.scalars(select(Customer).where(
        Customer.id.in_({r.customer_id for r in rows})))} if rows else {}
    invs = {i.id: i.number for i in db.scalars(select(Invoice).where(Invoice.id.in_({r.invoice_id for r in rows})))} if rows else {}
    return {"items": [{"id": str(r.id), "run_date": r.run_date.isoformat(), "customer_id": str(r.customer_id),
                       "customer_name": names.get(r.customer_id), "invoice_id": str(r.invoice_id),
                       "invoice_number": invs.get(r.invoice_id), "step": step_label(r.offset_days),
                       "sent": r.message_id is not None, "note": r.note} for r in rows],
            "total": total, "page": page, "size": size}


@router.put("/customers/{customer_id}/reminders")
def pause_customer(customer_id: uuid.UUID, body: PauseIn, db: Session = Depends(get_db),
                   user: User = Depends(collectors)):
    """FR-M8-10."""
    c = get_customer_or_404(db, user, customer_id)
    c.reminders_paused = body.paused
    audit.record(db, user, "customer", c.id, "update", new={"reminders_paused": body.paused})
    db.commit()
    return {"customer_id": str(c.id), "reminders_paused": c.reminders_paused}


@router.put("/invoices/{invoice_id}/reminders")
def pause_invoice(invoice_id: uuid.UUID, body: PauseIn, db: Session = Depends(get_db),
                  user: User = Depends(collectors)):
    from app.api.v1.invoices import get_invoice_or_404

    inv = get_invoice_or_404(db, user, invoice_id)
    inv.reminders_paused = body.paused
    audit.record(db, user, "invoice", inv.id, "update", new={"reminders_paused": body.paused})
    db.commit()
    return {"invoice_id": str(inv.id), "reminders_paused": inv.reminders_paused}


# ---------------- reports ----------------
def _visible_customers(db: Session, user: User) -> list[uuid.UUID] | None:
    if user.role != Role.sales:
        return None
    return list(db.scalars(customers_scoped(select(Customer.id), user)))


def _plain(v):
    """Money as exact strings, like every other endpoint."""
    if isinstance(v, Decimal):
        return str(v)
    if isinstance(v, uuid.UUID):
        return str(v)
    if isinstance(v, dict):
        return {k: _plain(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_plain(x) for x in v]
    return v


@router.get("/collections/dashboard")
def collections_dashboard(db: Session = Depends(get_db), user: User = Depends(staff)):
    return _plain(svc.dashboard(db, user.tenant_id))


@router.get("/collections/ageing")
def ageing(as_of: date | None = None, db: Session = Depends(get_db), user: User = Depends(staff)):
    return _plain(svc.ageing(db, user.tenant_id, _visible_customers(db, user), as_of))


@router.get("/collections/ageing/export")
def ageing_export(as_of: date | None = None, db: Session = Depends(get_db), user: User = Depends(staff)):
    rows = svc.ageing(db, user.tenant_id, _visible_customers(db, user), as_of)
    headers = ["customer", "whatsapp", "invoices", "not_due", "0_30_days", "31_60_days", "61_90_days",
               "over_90_days", "overdue", "total"]
    data = [[r["customer_name"], r["whatsapp_number"], r["invoices"], *[float(r[b]) for b in svc.BUCKETS],
             float(r["overdue"]), float(r["total"])] for r in rows]
    return Response(excel.export_rows(headers, data, "Ageing"), media_type=excel.XLSX_MIME,
                    headers={"Content-Disposition": 'attachment; filename="ageing.xlsx"'})
