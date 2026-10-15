"""Order management (SRS section 9, M6).

Every order, whatever its source, follows one status flow (TRANSITIONS). Any other move is rejected.
Buyer-facing status changes send exactly one WhatsApp message each; if WhatsApp can't be used
(not connected, opted out, window closed and no template) the order still moves and the reason
is recorded on the order timeline.

Callers must use commit_and_send(db) instead of db.commit() so queued WhatsApp messages are
handed to the worker only after the order change is saved.
"""

import re
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.models import (
    Customer,
    CustomerStatus,
    MessageTemplate,
    Order,
    OrderEvent,
    OrderLine,
    OrderSequence,
    OrderSource,
    OrderStatus,
    Product,
    SenderKind,
    StockStatus,
    Tenant,
    TemplateStatus,
    User,
)
from app.services import audit
from app.services.pricing import PricingEngine, money

S = OrderStatus

TRANSITIONS: dict[OrderStatus, set[OrderStatus]] = {
    S.draft: {S.quoted, S.confirmed, S.cancelled},
    S.quoted: {S.confirmed, S.expired, S.cancelled},
    S.expired: {S.quoted, S.cancelled},
    S.confirmed: {S.approved, S.on_hold, S.cancelled},
    S.on_hold: {S.approved, S.cancelled},
    S.approved: {S.invoiced, S.cancelled},
    S.invoiced: {S.dispatched},
    S.dispatched: {S.delivered},
    S.delivered: set(),
    S.cancelled: set(),
}
EDITABLE = {S.draft, S.quoted, S.expired, S.confirmed, S.on_hold}
BUYER_CAN_CANCEL = {S.quoted, S.expired, S.confirmed, S.on_hold}
# orders that count towards what the buyer owes (until the M7 ledger takes over)
EXPOSURE = (S.approved, S.invoiced, S.dispatched, S.delivered)

LABELS = {
    S.draft: "Draft", S.quoted: "Quoted", S.expired: "Expired", S.confirmed: "Confirmed", S.on_hold: "On hold",
    S.approved: "Approved", S.invoiced: "Invoiced", S.dispatched: "Dispatched", S.delivered: "Delivered",
    S.cancelled: "Cancelled",
}
IST = ZoneInfo("Asia/Kolkata")
TEMPLATE_NAME = "order_update"
BODY_LIMIT = 1024  # WhatsApp interactive body limit


class OrderError(Exception):
    def __init__(self, message: str, code: str = "invalid", http: int = 400):
        super().__init__(message)
        self.message = message
        self.code = code
        self.http = http


def label(status: OrderStatus) -> str:
    return LABELS[status]


# ---------------- numbering ----------------
def financial_year(on: date, start_month: int = 4) -> str:
    start = on.year if on.month >= start_month else on.year - 1
    if start_month == 1:
        return f"{start % 100:02d}"
    return f"{start % 100:02d}-{(start + 1) % 100:02d}"


def next_number(db: Session, tenant: Tenant, on: date | None = None) -> str:
    """ORD/26-27/00001 - unique per tenant, restarts every financial year (FR-M6-04)."""
    on = on or datetime.now(IST).date()
    fy = financial_year(on, tenant.fy_start_month or 4)
    db.execute(
        pg_insert(OrderSequence)
        .values(id=uuid.uuid4(), tenant_id=tenant.id, fy=fy, last_number=0)
        .on_conflict_do_nothing(constraint="uq_order_seq_fy")
    )
    seq = db.scalar(
        select(OrderSequence).where(OrderSequence.tenant_id == tenant.id, OrderSequence.fy == fy).with_for_update()
    )
    seq.last_number += 1
    return f"{tenant.order_prefix or 'ORD'}/{fy}/{seq.last_number:05d}"


# ---------------- pricing ----------------
def load_products(db: Session, tenant_id: uuid.UUID, ids: list[uuid.UUID]) -> dict[uuid.UUID, Product]:
    products = {p.id: p for p in db.scalars(select(Product).where(Product.tenant_id == tenant_id, Product.id.in_(ids)))}
    missing = [i for i in ids if i not in products]
    if missing:
        raise OrderError("One or more products were not found")
    inactive = [p.name for p in products.values() if not p.is_active]
    if inactive:
        raise OrderError(f"Inactive product(s) can't be ordered: {', '.join(sorted(set(inactive)))}")
    return products


def _set_lines(db: Session, order: Order, customer: Customer, items: list[dict]) -> None:
    """items: [{"product": Product, "quantity": Decimal, "match_confidence"?, "raw_text"?}] - prices come from M3."""
    if not items:
        raise OrderError("Add at least one product")
    for it in items:
        if it["quantity"] <= 0:
            raise OrderError("Quantity must be more than zero")
    engine = PricingEngine(db, customer)
    order.lines.clear()
    db.flush()
    gross = discount = taxable = gst = Decimal(0)
    for pos, it in enumerate(items, start=1):
        p: Product = it["product"]
        priced = engine.price_line(p, Decimal(it["quantity"]))
        order.lines.append(OrderLine(
            tenant_id=order.tenant_id, position=pos, product_id=p.id, sku=p.sku, name=p.name, uom=p.uom,
            hsn_code=p.hsn_code, quantity=priced["quantity"], free_qty=priced["free_qty"],
            unit_price=priced["unit_price"], price_source=priced["price_source"], gross=priced["gross"],
            discount=priced["discount"], scheme_id=priced["scheme_id"], scheme_name=priced["scheme_name"],
            taxable_value=priced["taxable_value"], gst_rate=priced["gst_rate"], gst_amount=priced["gst_amount"],
            line_total=priced["line_total"], out_of_stock=p.stock_status == StockStatus.out_of_stock,
            match_confidence=it.get("match_confidence"), raw_text=it.get("raw_text"),
        ))
        gross += priced["gross"]
        discount += priced["discount"]
        taxable += priced["taxable_value"]
        gst += priced["gst_amount"]
    order.gross_total = money(gross)
    order.discount_total = money(discount)
    order.taxable_total = money(taxable)
    order.gst_total = money(gst)
    order.grand_total = money(taxable + gst)


def _lines_snapshot(order: Order) -> list[dict]:
    return [{"sku": l.sku, "quantity": str(l.quantity), "unit_price": str(l.unit_price),
             "line_total": str(l.line_total)} for l in order.lines]


# ---------------- credit ----------------
def credit_exposure(db: Session, customer: Customer, exclude_order_id: uuid.UUID | None = None) -> Decimal:
    """What the buyer owes now: opening balance + approved/invoiced orders.

    Payments and credit notes arrive with M7/M8; then this becomes the ledger balance.
    """
    q = select(func.coalesce(func.sum(Order.grand_total), 0)).where(
        Order.tenant_id == customer.tenant_id, Order.customer_id == customer.id, Order.status.in_(EXPOSURE))
    if exclude_order_id:
        q = q.where(Order.id != exclude_order_id)
    return money(Decimal(customer.opening_balance or 0) + Decimal(db.scalar(q)))


def hold_reasons(db: Session, order: Order, customer: Customer) -> list[str]:
    reasons: list[str] = []
    limit = Decimal(customer.credit_limit or 0)
    if limit > 0:  # 0 = no credit limit set
        outstanding = credit_exposure(db, customer, order.id)
        if outstanding + order.grand_total > limit:
            reasons.append(
                f"Over credit limit: outstanding ₹{outstanding:,.2f} + this order ₹{order.grand_total:,.2f} "
                f"exceeds limit ₹{limit:,.2f}"
            )
    oos = [l.name for l in order.lines if l.out_of_stock]
    if oos:
        reasons.append("Out of stock: " + ", ".join(oos))
    return reasons


# ---------------- buyer messages ----------------
def _fmt_qty(q: Decimal) -> str:
    q = Decimal(q).normalize()
    return f"{q:f}"


def _fmt_time(dt: datetime | None) -> str:
    if dt is None:
        return ""
    return dt.astimezone(IST).strftime("%d %b, %I:%M %p").replace(" 0", " ")


def quote_text(order: Order, revised: bool = False) -> str:
    head = f"{'Revised quote' if revised else 'Quote'} for order {order.number}"
    rows: list[str] = []
    for i, l in enumerate(order.lines, start=1):
        row = f"{i}. {l.name} × {_fmt_qty(l.quantity)} {l.uom} @ ₹{l.unit_price:,.2f} = ₹{l.gross:,.2f}"
        if l.discount > 0:
            row += f"\n   {l.scheme_name or 'Scheme'}: −₹{l.discount:,.2f}"
        if l.free_qty > 0:
            row += f"\n   Free: {_fmt_qty(l.free_qty)} {l.uom} ({l.scheme_name or 'scheme'})"
        if l.out_of_stock:
            row += "\n   (currently out of stock)"
        rows.append(row)
    foot = [f"Taxable value: ₹{order.taxable_total:,.2f}", f"GST: ₹{order.gst_total:,.2f}",
            f"*Total: ₹{order.grand_total:,.2f}*"]
    if order.ai_unclear:
        unclear = []
        for u in order.ai_unclear[:5]:
            names = [c["name"] for c in (u.get("candidates") or [])[:2]]
            if u.get("reason") == "quantity missing" and names:
                unclear.append(f"• {names[0]}: how many?")
            else:
                unclear.append(f"• {u.get('text')}" + (f": did you mean {' or '.join(names)}?" if names else ": not found"))
        foot = ["Not added yet (reply to clarify):", *unclear, "", *foot]
    if order.quote_valid_until:
        foot.append(f"Valid till {_fmt_time(order.quote_valid_until)}.")
    foot.append("Tap Confirm to place the order.")

    def build(shown: list[str], hidden: int) -> str:
        body = [head, "", *shown]
        if hidden:
            body.append(f"…and {hidden} more item(s)")
        return "\n".join([*body, "", *foot])

    text = build(rows, 0)
    n = len(rows)
    while len(text) > BODY_LIMIT and n > 1:
        n -= 1
        text = build(rows[:n], len(rows) - n)
    return text[:BODY_LIMIT]


def status_text(order: Order, status: OrderStatus) -> str:
    n = order.number
    if status == S.expired:
        return f"Your quote for order {n} has expired. Reply here if you'd still like to order and we'll send a fresh quote."
    if status == S.confirmed:
        return (f"Thank you! We've received your order {n} for ₹{order.grand_total:,.2f}. "
                "We'll let you know once it's approved.")
    if status == S.approved:
        return f"Your order {n} is approved and is being prepared."
    if status == S.invoiced:
        inv = f" Invoice {order.invoice_number}." if order.invoice_number else ""
        short = [l for l in order.lines if l.invoiced_qty is not None and l.invoiced_qty < l.quantity]
        extra = ""
        if short:
            extra = " Short supply: " + "; ".join(
                f"{l.name} {_fmt_qty(l.invoiced_qty)} of {_fmt_qty(l.quantity)}" for l in short) + "."
        return f"Your order {n} has been invoiced.{inv}{extra}"
    if status == S.dispatched:
        bits = [b for b in (
            f"Vehicle {order.vehicle_number}" if order.vehicle_number else None,
            f"Transporter {order.transporter}" if order.transporter else None,
            f"LR No. {order.lr_number}" if order.lr_number else None) if b]
        return f"Your order {n} has been dispatched." + (" " + ", ".join(bits) + "." if bits else "")
    if status == S.delivered:
        return f"Your order {n} has been delivered. Thank you for your business!"
    if status == S.cancelled:
        if order.cancelled_by_buyer:
            return f"Your order {n} has been cancelled as requested."
        reason = f" Reason: {order.cancel_reason}" if order.cancel_reason else ""
        return f"Your order {n} has been cancelled.{reason}"
    return f"Update on your order {n}: {label(status)}."


def _queue_buyer_message(db: Session, order: Order, status: OrderStatus, *, revised: bool = False):
    """Returns (message, note). Never raises: a failed notification must not block the order."""
    from app.services.whatsapp import outbound
    from app.services.whatsapp.inbound import get_or_create_conversation

    customer = db.get(Customer, order.customer_id)
    try:
        outbound.get_account(db, order.tenant_id)
        if customer.opted_out:
            return None, "Not sent: customer opted out of WhatsApp"
        conv = get_or_create_conversation(db, customer)
        if outbound.window_open(conv):
            if status == S.quoted:
                ref = str(order.id)
                msg = outbound.queue_buttons(db, customer, quote_text(order, revised), [
                    {"id": f"order:confirm:{ref}", "title": "Confirm"},
                    {"id": f"order:edit:{ref}", "title": "Edit"},
                    {"id": f"order:cancel:{ref}", "title": "Cancel"},
                ], sender=SenderKind.system)
            else:
                msg = outbound.queue_text(db, customer, status_text(order, status), sender=SenderKind.system)
            return msg, None
        tpl = db.scalar(select(MessageTemplate).where(
            MessageTemplate.tenant_id == order.tenant_id, MessageTemplate.name == TEMPLATE_NAME,
            MessageTemplate.status == TemplateStatus.approved))
        if tpl is None:
            return None, (f"Not sent: no chat in the last 24 hours and no approved '{TEMPLATE_NAME}' template")
        params = [customer.contact_person or customer.business_name, order.number][: tpl.variable_count]
        msg = outbound.queue_template(db, customer, tpl, params, sender=SenderKind.system)
        return msg, None
    except outbound.SendBlocked as exc:
        return None, f"Not sent: {exc.message}"


def _pending(db: Session) -> list[uuid.UUID]:
    return db.info.setdefault("pending_sends", [])


def commit_and_send(db: Session) -> None:
    """Commit, then hand queued WhatsApp messages to the worker."""
    db.commit()
    ids = list(_pending(db))
    _pending(db).clear()
    if ids:
        from app.worker import tasks

        for mid in ids:
            tasks.send_message.delay(str(mid))


def _event(db: Session, order: Order, kind: str, *, frm: OrderStatus | None = None, to: OrderStatus | None = None,
           user: User | None = None, actor: str | None = None, remark: str | None = None,
           notify: bool = False, revised: bool = False) -> OrderEvent:
    ev = OrderEvent(
        tenant_id=order.tenant_id, order_id=order.id, kind=kind,
        from_status=frm.value if frm else None, to_status=to.value if to else None,
        actor=actor or ("user" if user else "system"), user_id=user.id if user else None, remark=remark,
    )
    if notify and to is not None:
        msg, note = _queue_buyer_message(db, order, to, revised=revised)
        if msg is not None:
            ev.message_id = msg.id
            _pending(db).append(msg.id)
        ev.notify_note = note
    db.add(ev)
    return ev


def _move(db: Session, order: Order, to: OrderStatus, *, user: User | None = None, actor: str | None = None,
          remark: str | None = None, notify: bool = True, revised: bool = False) -> None:
    frm = order.status
    if to not in TRANSITIONS[frm]:
        raise OrderError(f"Cannot move order from {label(frm)} to {label(to)}", "invalid_transition", 409)
    order.status = to
    db.flush()
    _event(db, order, "status", frm=frm, to=to, user=user, actor=actor, remark=remark, notify=notify, revised=revised)
    audit.record(db, user, "order", order.id, "update", {"status": frm.value}, {"status": to.value},
                 tenant_id=order.tenant_id)


# ---------------- actions ----------------
def create_order(db: Session, user: User | None, customer: Customer, items: list[dict], *,
                 source: OrderSource = OrderSource.panel, notes: str | None = None,
                 source_message_id: uuid.UUID | None = None) -> Order:
    if customer.status == CustomerStatus.inactive:
        raise OrderError("This customer is inactive")
    tenant = db.get(Tenant, customer.tenant_id)
    order = Order(
        tenant_id=customer.tenant_id, number=next_number(db, tenant), customer_id=customer.id,
        sales_user_id=customer.assigned_user_id, created_by_user_id=user.id if user else None,
        source=source, source_message_id=source_message_id, status=S.draft, notes=notes, lines=[],
    )
    db.add(order)
    db.flush()
    _set_lines(db, order, customer, items)
    db.flush()
    _event(db, order, "created", to=S.draft, user=user, actor=None if user else "system",
           remark={OrderSource.whatsapp: "Created by AI from a WhatsApp message", OrderSource.panel: "Created in panel",
                   OrderSource.excel: "Imported from Excel"}[source])
    audit.record(db, user, "order", order.id, "create",
                 new={"number": order.number, "customer_id": str(customer.id), "lines": _lines_snapshot(order),
                      "grand_total": str(order.grand_total)}, tenant_id=order.tenant_id)
    return order


def _quote_validity(db: Session, order: Order) -> datetime:
    tenant = db.get(Tenant, order.tenant_id)
    return datetime.now(UTC) + timedelta(hours=tenant.quote_validity_hours or 24)


def send_quote(db: Session, user: User | None, order: Order, *, revised: bool = False) -> Order:
    if not order.lines:
        raise OrderError("Add at least one product before sending a quote")
    order.quoted_at = datetime.now(UTC)
    order.quote_valid_until = _quote_validity(db, order)
    if order.status == S.quoted:  # re-send the same quote with fresh validity
        _event(db, order, "status", frm=S.quoted, to=S.quoted, user=user, remark="Quote re-sent",
               notify=True, revised=revised)
        return order
    _move(db, order, S.quoted, user=user, revised=revised)
    return order


def edit_lines(db: Session, user: User | None, order: Order, items: list[dict], notes: str | None = None,
               actor: str | None = None) -> Order:
    """FR-M6-08: edit until Approved; audited; buyer gets the revised quote to re-confirm."""
    if order.status not in EDITABLE:
        raise OrderError(f"A {label(order.status)} order can't be edited", "not_editable", 409)
    customer = db.get(Customer, order.customer_id)
    old = {"lines": _lines_snapshot(order), "grand_total": str(order.grand_total)}
    _set_lines(db, order, customer, items)
    if notes is not None:
        order.notes = notes
    order.revision += 1
    db.flush()
    new = {"lines": _lines_snapshot(order), "grand_total": str(order.grand_total)}
    audit.record(db, user, "order", order.id, "update", old, new, tenant_id=order.tenant_id)
    _event(db, order, "edit", user=user, actor=actor,
           remark=f"{'Buyer changed items on WhatsApp' if actor == 'buyer' else 'Lines edited'} (revision {order.revision})")
    if order.status != S.draft:
        frm = order.status
        order.confirmed_at = None
        order.confirmed_by_buyer = False
        order.hold_reasons = None
        order.quoted_at = datetime.now(UTC)
        order.quote_valid_until = _quote_validity(db, order)
        if frm == S.quoted:
            _event(db, order, "status", frm=S.quoted, to=S.quoted, user=user, actor=actor, remark="Revised quote sent",
                   notify=True, revised=True)
        else:
            # back to Quoted so the buyer re-confirms; bypasses the normal map on purpose
            order.status = S.quoted
            db.flush()
            _event(db, order, "status", frm=frm, to=S.quoted, user=user, actor=actor, remark="Revised quote sent",
                   notify=True, revised=True)
    return order


def _expired(order: Order) -> bool:
    return (order.status == S.quoted and order.quote_valid_until is not None
            and order.quote_valid_until < datetime.now(UTC))


def expire(db: Session, order: Order) -> None:
    _move(db, order, S.expired, actor="system", remark="Quote validity ended")


def confirm(db: Session, order: Order, *, user: User | None = None, by_buyer: bool = False) -> Order:
    """FR-M6-06: credit and stock check on Confirm; problems put the order On hold."""
    if by_buyer and order.status != S.quoted:
        raise OrderError(f"Order {order.number} is already {label(order.status).lower()}.", "invalid_transition", 409)
    if _expired(order):
        expire(db, order)
        raise OrderError("The quote has expired. Send a fresh quote first.", "quote_expired", 409)
    _move(db, order, S.confirmed, user=user, actor="buyer" if by_buyer else None,
          remark="Confirmed by buyer on WhatsApp" if by_buyer else "Confirmed on buyer's instruction")
    order.confirmed_at = datetime.now(UTC)
    order.confirmed_by_buyer = by_buyer
    customer = db.get(Customer, order.customer_id)
    reasons = hold_reasons(db, order, customer)
    if reasons:
        order.hold_reasons = "; ".join(reasons)[:500]
        _move(db, order, S.on_hold, actor="system", remark=order.hold_reasons, notify=False)
    return order


def approve(db: Session, user: User, order: Order, remark: str | None = None) -> Order:
    if order.status == S.on_hold and not (remark and remark.strip()):
        raise OrderError("Add a remark when approving an order that is on hold")
    _move(db, order, S.approved, user=user, remark=remark)
    order.approved_at = datetime.now(UTC)
    order.approved_by_user_id = user.id
    order.approval_remark = remark
    return order


def reject(db: Session, user: User, order: Order, remark: str) -> Order:
    if order.status != S.on_hold:
        raise OrderError("Only orders on hold can be rejected", "invalid_transition", 409)
    if not remark or not remark.strip():
        raise OrderError("Add a remark explaining the rejection")
    order.approval_remark = remark
    return cancel(db, order, f"Not approved: {remark}", user=user)


def cancel(db: Session, order: Order, reason: str | None, *, user: User | None = None, by_buyer: bool = False) -> Order:
    if by_buyer:
        if order.status not in BUYER_CAN_CANCEL:
            raise OrderError(f"This order is {label(order.status)} and can no longer be cancelled from WhatsApp",
                             "invalid_transition", 409)
    elif not (reason and reason.strip()):
        raise OrderError("Add a reason for cancelling")
    order.cancel_reason = (reason or "Cancelled by buyer")[:500]
    order.cancelled_by_buyer = by_buyer
    order.cancelled_at = datetime.now(UTC)
    _move(db, order, S.cancelled, user=user, actor="buyer" if by_buyer else None, remark=order.cancel_reason)
    return order


def mark_invoiced(db: Session, user: User, order: Order, invoice_number: str, invoice_date: date | None,
                  invoiced: dict[uuid.UUID, Decimal]) -> Order:
    """FR-M6-09: one invoice per order; invoiced qty may be lower than ordered (short supply)."""
    if order.status != S.approved:
        raise OrderError(f"Cannot move order from {label(order.status)} to Invoiced", "invalid_transition", 409)
    ids = {l.id for l in order.lines}
    if set(invoiced) - ids:
        raise OrderError("Unknown order line")
    for l in order.lines:
        qty = invoiced.get(l.id, l.quantity)
        if qty < 0 or qty > l.quantity:
            raise OrderError(f"Invoiced quantity for {l.name} must be between 0 and {_fmt_qty(l.quantity)}")
        l.invoiced_qty = qty
    order.invoice_number = invoice_number.strip()
    order.invoice_date = invoice_date or datetime.now(IST).date()
    order.invoiced_at = datetime.now(UTC)
    short = [l for l in order.lines if l.invoiced_qty < l.quantity]
    remark = f"Invoice {order.invoice_number}"
    if short:
        remark += "; short supply: " + ", ".join(f"{l.sku} {_fmt_qty(l.quantity - l.invoiced_qty)}" for l in short)
    _move(db, order, S.invoiced, user=user, remark=remark)
    return order


def dispatch(db: Session, user: User, order: Order, vehicle_number: str | None, transporter: str | None,
             lr_number: str | None) -> Order:
    order.vehicle_number = (vehicle_number or "").strip().upper() or None
    order.transporter = (transporter or "").strip() or None
    order.lr_number = (lr_number or "").strip() or None
    order.dispatched_at = datetime.now(UTC)
    _move(db, order, S.dispatched, user=user)
    return order


def deliver(db: Session, user: User | None, order: Order, *, by_buyer: bool = False) -> Order:
    _move(db, order, S.delivered, user=user, actor="buyer" if by_buyer else None)
    order.delivered_at = datetime.now(UTC)
    return order


def expire_due_quotes(db: Session) -> int:
    due = db.scalars(select(Order).where(Order.status == S.quoted, Order.quote_valid_until < datetime.now(UTC))).all()
    for o in due:
        expire(db, o)
    return len(due)


# ---------------- WhatsApp bot hooks ----------------
STATUS_QUERY = re.compile(
    r"(where\s+is\s+my\s+order|order\s+status|status\s+of\s+(my\s+)?order|track\s+(my\s+)?order|my\s+orders?\b"
    r"|^\s*status\s*\??\s*$|order\s+(enga|enge|eppo|kahan|kaha|kab)|mera\s+order)",
    re.IGNORECASE,
)


def is_status_query(text: str | None) -> bool:
    return bool(text and STATUS_QUERY.search(text))


def status_reply(db: Session, customer: Customer) -> str:
    """FR-M6-10: current status of the buyer's recent orders."""
    orders = db.scalars(
        select(Order).where(Order.tenant_id == customer.tenant_id, Order.customer_id == customer.id,
                            Order.status != S.draft)
        .order_by(Order.created_at.desc()).limit(5)
    ).all()
    if not orders:
        return "We couldn't find any recent orders for you. Reply here and our team will help."
    rows = []
    for o in orders:
        row = f"• {o.number} ({o.created_at.astimezone(IST).strftime('%d %b')}, ₹{o.grand_total:,.2f}): {label(o.status)}"
        if o.status == S.dispatched:
            bits = [b for b in (o.vehicle_number, f"LR {o.lr_number}" if o.lr_number else None) if b]
            if bits:
                row += f" – {', '.join(bits)}"
        if o.status == S.on_hold:
            row = row.replace(label(S.on_hold), "Awaiting approval")
        if o.status == S.quoted:
            row += " – waiting for your confirmation"
        rows.append(row)
    return "Your recent orders:\n" + "\n".join(rows)


def parse_button(reply_id: str | None) -> tuple[str, uuid.UUID] | None:
    if not reply_id or not reply_id.startswith("order:"):
        return None
    parts = reply_id.split(":")
    if len(parts) != 3 or parts[1] not in {"confirm", "edit", "cancel"}:
        return None
    try:
        return parts[1], uuid.UUID(parts[2])
    except ValueError:
        return None
