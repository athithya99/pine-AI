"""Fixed, tenant- and role-scoped queries behind the business assistant, daily summary and dashboard (M10).

The AI never writes SQL: it only picks one of these functions and its parameters (see services/ai/business.py),
so every figure in an answer comes from the database, a Sales user only ever sees their own customers, and nothing
can read another company's data. Each function returns {"text", "columns", "rows"}.
"""

import re
import uuid
from calendar import monthrange
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import case, func, or_, select
from sqlalchemy.orm import Session

from app.models import (
    Conversation,
    Customer,
    CustomerStatus,
    Invoice,
    InvoiceStatus,
    LedgerEntry,
    Order,
    OrderLine,
    OrderSource,
    OrderStatus,
    Payment,
    PaymentStatus,
    Product,
    Role,
    StockStatus,
    User,
)

IST = ZoneInfo("Asia/Kolkata")
PLACED = (OrderStatus.confirmed, OrderStatus.on_hold, OrderStatus.approved, OrderStatus.invoiced,
          OrderStatus.dispatched, OrderStatus.delivered)
MAX_ROWS = 500


def today() -> date:
    return datetime.now(IST).date()


def money(v) -> Decimal:
    return Decimal(v or 0).quantize(Decimal("0.01"))


def inr(v) -> str:
    """Indian grouping: 1234567.5 -> 12,34,567.50"""
    d = money(v)
    sign = "-" if d < 0 else ""
    whole, frac = f"{abs(d):.2f}".split(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        head = ",".join(re.findall(r"\d{1,2}", head[::-1]))[::-1] if head else ""
        whole = f"{head},{tail}"
    return f"{sign}₹{whole}.{frac}"


# ---------------------------------------------------------------- periods
@dataclass
class Period:
    start: date
    end: date  # inclusive
    label: str

    @property
    def start_dt(self) -> datetime:
        return datetime.combine(self.start, time.min, IST)

    @property
    def end_dt(self) -> datetime:  # exclusive
        return datetime.combine(self.end + timedelta(days=1), time.min, IST)


MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
          "november", "december"]
PERIOD_KEYS = ["today", "yesterday", "this_week", "last_week", "this_month", "last_month", "this_year",
               "last_7_days", "last_30_days", "last_90_days", "next_7_days"]


def fy_start(on: date, start_month: int = 4) -> date:
    y = on.year if on.month >= start_month else on.year - 1
    return date(y, start_month, 1)


def period(key: str | None, *, days: int | None = None, start: date | None = None, end: date | None = None,
           on: date | None = None) -> Period:
    d = on or today()
    if start and end:
        return Period(start, end, f"{start:%d %b %Y} – {end:%d %b %Y}")
    key = (key or "this_month").lower()
    if key == "last_n_days" and days:
        return Period(d - timedelta(days=days - 1), d, f"in the last {days} days")
    if key == "today":
        return Period(d, d, "today")
    if key == "yesterday":
        y = d - timedelta(days=1)
        return Period(y, y, "yesterday")
    if key == "this_week":
        s = d - timedelta(days=d.weekday())
        return Period(s, d, "this week")
    if key == "last_week":
        s = d - timedelta(days=d.weekday() + 7)
        return Period(s, s + timedelta(days=6), "last week")
    if key == "last_month":
        first = d.replace(day=1)
        last_end = first - timedelta(days=1)
        return Period(last_end.replace(day=1), last_end, f"in {last_end:%B %Y}")
    if key == "this_year":
        return Period(fy_start(d), d, "this financial year")
    if key == "next_7_days":
        return Period(d, d + timedelta(days=6), "in the next 7 days")
    m = re.fullmatch(r"last_(\d+)_days", key)
    if m:
        n = int(m.group(1))
        return Period(d - timedelta(days=n - 1), d, f"in the last {n} days")
    m = re.fullmatch(r"month_(\d{1,2})", key)  # a named month: the latest one not in the future
    if m:
        mo = int(m.group(1))
        y = d.year if mo <= d.month else d.year - 1
        last = date(y, mo, monthrange(y, mo)[1])
        return Period(date(y, mo, 1), min(last, d), f"in {date(y, mo, 1):%B %Y}")
    return Period(d.replace(day=1), d, "this month")


def period_from_text(text: str, default: str = "this_month", on: date | None = None) -> Period:
    t = (text or "").lower()
    if m := re.search(r"(?:last|past|previous)\s+(\d{1,3})\s+days?", t):
        return period("last_n_days", days=int(m.group(1)), on=on)
    for words, key in ((r"\btoday\b|\btoday's\b", "today"), (r"\byesterday\b", "yesterday"),
                       (r"\bthis\s+week\b", "this_week"), (r"\blast\s+week\b", "last_week"),
                       (r"\blast\s+month\b|\bprevious\s+month\b", "last_month"),
                       (r"\bthis\s+month\b|\bmonth\s+so\s+far\b", "this_month"),
                       (r"\bthis\s+year\b|\bthis\s+(fy|financial\s+year)\b|\bytd\b", "this_year"),
                       (r"\bnext\s+(7\s+days|week)\b", "next_7_days")):
        if re.search(words, t):
            return period(key, on=on)
    for i, name in enumerate(MONTHS, start=1):
        if re.search(rf"\b(in|for|during)\s+({name}|{name[:3]})\b", t):
            return period(f"month_{i}", on=on)
    return period(default, on=on)


# ---------------------------------------------------------------- scoping
def _cust_scope(stmt, user: User):
    """Tenant + (for Sales) only their assigned customers (FR-M10-07). Needs Customer in the FROM clause."""
    stmt = stmt.where(Customer.tenant_id == user.tenant_id)
    if user.role == Role.sales:
        stmt = stmt.where(Customer.assigned_user_id == user.id)
    return stmt


def _answer(text: str, columns: list[tuple[str, str, str]] | None = None, rows: list[dict] | None = None) -> dict:
    return {"text": text, "columns": [{"key": k, "label": lab, "type": typ} for k, lab, typ in (columns or [])],
            "rows": (rows or [])[:MAX_ROWS], "total_rows": len(rows or [])}


def _plural(n: int, word: str, many: str | None = None) -> str:
    return f"{n} {word if n == 1 else (many or word + 's')}"


# ---------------------------------------------------------------- tools
def overdue_customers(db: Session, user: User, min_days: int = 0) -> dict:
    """Customers with invoices more than N days past their due date."""
    cutoff = today() - timedelta(days=max(min_days, 0))
    due = func.coalesce(Invoice.due_date, Invoice.invoice_date)
    stmt = (select(Customer.id, Customer.business_name, func.sum(Invoice.total - Invoice.amount_paid),
                   func.min(due), func.count(Invoice.id))
            .join(Invoice, Invoice.customer_id == Customer.id)
            .where(Invoice.status == InvoiceStatus.issued, Invoice.total > Invoice.amount_paid, due < cutoff)
            .group_by(Customer.id, Customer.business_name).order_by(func.sum(Invoice.total - Invoice.amount_paid).desc()))
    rows = [{"customer_id": str(cid), "customer": name, "overdue_amount": money(amt), "invoices": n,
             "oldest_due": oldest, "days_overdue": (today() - oldest).days}
            for cid, name, amt, oldest, n in db.execute(_cust_scope(stmt, user)).all()]
    total = sum((r["overdue_amount"] for r in rows), Decimal(0))
    what = f"more than {min_days} days overdue" if min_days else "overdue"
    if not rows:
        return _answer(f"No customer has invoices {what}.")
    top = rows[0]
    text = (f"{_plural(len(rows), 'customer')} {'has' if len(rows) == 1 else 'have'} invoices {what}, "
            f"{inr(total)} in total. Highest: {top['customer']} ({inr(top['overdue_amount'])}, "
            f"oldest {top['days_overdue']} days).")
    return _answer(text, [("customer", "Customer", "text"), ("overdue_amount", "Overdue", "money"),
                          ("invoices", "Invoices", "int"), ("oldest_due", "Oldest due date", "date"),
                          ("days_overdue", "Days overdue", "int")], rows)


def top_customers(db: Session, user: User, p: Period, limit: int = 10) -> dict:
    """Customers ranked by value of orders placed in the period."""
    stmt = (select(Customer.id, Customer.business_name, func.sum(Order.grand_total), func.count(Order.id))
            .join(Order, Order.customer_id == Customer.id)
            .where(Order.status.in_(PLACED), Order.created_at >= p.start_dt, Order.created_at < p.end_dt)
            .group_by(Customer.id, Customer.business_name).order_by(func.sum(Order.grand_total).desc()))
    all_rows = db.execute(_cust_scope(stmt, user)).all()
    rows = [{"customer_id": str(cid), "customer": name, "order_value": money(v), "orders": n}
            for cid, name, v, n in all_rows[:limit]]
    if not rows:
        return _answer(f"No orders were placed {p.label}.")
    total = sum((money(v) for _, _, v, _ in all_rows), Decimal(0))
    names = ", ".join(f"{r['customer']} ({inr(r['order_value'])})" for r in rows[:3])
    text = (f"Top {len(rows)} customer{'s' if len(rows) != 1 else ''} by order value {p.label}: {names}"
            f"{'…' if len(rows) > 3 else ''}. All customers together: {inr(total)}.")
    return _answer(text, [("customer", "Customer", "text"), ("order_value", "Order value", "money"),
                          ("orders", "Orders", "int")], rows)


def inactive_customers(db: Session, user: User, days: int = 30) -> dict:
    """Active customers with no order placed in the last N days (including those who never ordered)."""
    last = (select(Order.customer_id, func.max(Order.created_at).label("last_at"))
            .where(Order.tenant_id == user.tenant_id, Order.status.in_(PLACED)).group_by(Order.customer_id).subquery())
    since = datetime.now(UTC) - timedelta(days=days)
    stmt = (select(Customer.id, Customer.business_name, Customer.whatsapp_number, last.c.last_at)
            .outerjoin(last, last.c.customer_id == Customer.id)
            .where(Customer.status == CustomerStatus.active, or_(last.c.last_at.is_(None), last.c.last_at < since))
            .order_by(last.c.last_at.asc().nulls_first(), Customer.business_name))
    rows = []
    for cid, name, phone, last_at in db.execute(_cust_scope(stmt, user)).all():
        d = last_at.astimezone(IST).date() if last_at else None
        rows.append({"customer_id": str(cid), "customer": name, "whatsapp": phone, "last_order": d,
                     "days_since": (today() - d).days if d else None})
    if not rows:
        return _answer(f"Every active customer has ordered in the last {days} days.")
    never = sum(1 for r in rows if r["last_order"] is None)
    text = (f"{_plural(len(rows), 'customer')} {'has' if len(rows) == 1 else 'have'} not ordered in the last {days} days"
            + (f" ({never} never ordered)." if never else "."))
    return _answer(text, [("customer", "Customer", "text"), ("whatsapp", "WhatsApp", "text"),
                          ("last_order", "Last order", "date"), ("days_since", "Days since", "int")], rows)


def top_products(db: Session, user: User, p: Period, limit: int = 10) -> dict:
    """Products ranked by value ordered in the period."""
    stmt = (select(OrderLine.product_id, OrderLine.name, OrderLine.uom, func.sum(OrderLine.quantity),
                   func.sum(OrderLine.line_total))
            .join(Order, Order.id == OrderLine.order_id).join(Customer, Customer.id == Order.customer_id)
            .where(Order.status.in_(PLACED), Order.created_at >= p.start_dt, Order.created_at < p.end_dt)
            .group_by(OrderLine.product_id, OrderLine.name, OrderLine.uom)
            .order_by(func.sum(OrderLine.line_total).desc()))
    rows = [{"product": name, "quantity": Decimal(q or 0).normalize(), "uom": uom, "value": money(v)}
            for _, name, uom, q, v in db.execute(_cust_scope(stmt, user)).all()[:limit]]
    if not rows:
        return _answer(f"No products were ordered {p.label}.")
    text = (f"Top {len(rows)} product{'s' if len(rows) != 1 else ''} {p.label}: "
            + ", ".join(f"{r['product']} ({inr(r['value'])})" for r in rows[:3]) + ("…" if len(rows) > 3 else "."))
    return _answer(text, [("product", "Product", "text"), ("quantity", "Quantity", "number"), ("uom", "Unit", "text"),
                          ("value", "Value", "money")], rows)


SOURCE_LABEL = {OrderSource.whatsapp: "WhatsApp (AI)", OrderSource.panel: "Panel", OrderSource.excel: "Excel"}


def sales_summary(db: Session, user: User, p: Period) -> dict:
    """Orders placed in the period: count, value and split by source."""
    stmt = (select(Order.source, func.count(Order.id), func.sum(Order.grand_total))
            .join(Customer, Customer.id == Order.customer_id)
            .where(Order.status.in_(PLACED), Order.created_at >= p.start_dt, Order.created_at < p.end_dt)
            .group_by(Order.source))
    by = {s: (n, money(v)) for s, n, v in db.execute(_cust_scope(stmt, user)).all()}
    count = sum(n for n, _ in by.values())
    value = sum((v for _, v in by.values()), Decimal(0))
    rows = [{"source": SOURCE_LABEL[s], "orders": by.get(s, (0, 0))[0], "value": money(by.get(s, (0, 0))[1])}
            for s in OrderSource]
    if not count:
        return _answer(f"No orders were placed {p.label}.", [("source", "Source", "text"), ("orders", "Orders", "int"),
                                                              ("value", "Value", "money")], rows)
    text = (f"{_plural(count, 'order')} worth {inr(value)} {p.label} (average {inr(value / count)}). "
            f"WhatsApp AI: {by.get(OrderSource.whatsapp, (0, 0))[0]}, panel: {by.get(OrderSource.panel, (0, 0))[0]}, "
            f"Excel: {by.get(OrderSource.excel, (0, 0))[0]}.")
    return _answer(text, [("source", "Source", "text"), ("orders", "Orders", "int"), ("value", "Value", "money")], rows)


def collections_summary(db: Session, user: User, p: Period) -> dict:
    """Confirmed payments received in the period, by mode."""
    stmt = (select(Payment.mode, func.count(Payment.id), func.sum(Payment.amount))
            .join(Customer, Customer.id == Payment.customer_id)
            .where(Payment.status == PaymentStatus.confirmed, Payment.payment_date >= p.start,
                   Payment.payment_date <= p.end)
            .group_by(Payment.mode).order_by(func.sum(Payment.amount).desc()))
    rows = [{"mode": m.value.upper() if m.value in ("upi", "neft", "rtgs") else m.value.title(), "payments": n,
             "amount": money(v)} for m, n, v in db.execute(_cust_scope(stmt, user)).all()]
    total = sum((r["amount"] for r in rows), Decimal(0))
    if not rows:
        return _answer(f"No payments were received {p.label}.")
    n = sum(r["payments"] for r in rows)
    return _answer(f"{inr(total)} collected {p.label} from {_plural(n, 'payment')}.",
                   [("mode", "Mode", "text"), ("payments", "Payments", "int"), ("amount", "Amount", "money")], rows)


def _balances(db: Session, user: User):
    led = (select(LedgerEntry.customer_id, func.sum(LedgerEntry.debit - LedgerEntry.credit).label("net"))
           .where(LedgerEntry.tenant_id == user.tenant_id).group_by(LedgerEntry.customer_id).subquery())
    bal = (func.coalesce(Customer.opening_balance, 0) + func.coalesce(led.c.net, 0)).label("balance")
    return select(Customer.id, Customer.business_name, bal).outerjoin(led, led.c.customer_id == Customer.id), bal


def find_customers(db: Session, user: User, name: str) -> list[Customer]:
    name = (name or "").strip()
    if not name:
        return []
    stmt = _cust_scope(select(Customer).where(Customer.business_name.ilike(f"%{name}%")), user)
    found = list(db.scalars(stmt.order_by(Customer.business_name).limit(5)))
    if not found:  # "murugan" in "Sri Murugan Stores", "kumar provision" -> first word
        first = name.split()[0]
        if len(first) >= 4:
            found = list(db.scalars(_cust_scope(select(Customer).where(Customer.business_name.ilike(f"%{first}%")),
                                                user).limit(5)))
    return found


def outstanding(db: Session, user: User, customer: str | None = None) -> dict:
    """Total receivable and who owes most; or one customer's balance and unpaid invoices."""
    if customer:
        found = find_customers(db, user, customer)
        if not found:
            return _answer(f"I couldn't find a customer called \"{customer}\"" +
                           (" among your customers." if user.role == Role.sales else "."))
        if len(found) > 1 and not any(c.business_name.lower() == customer.strip().lower() for c in found):
            return _answer(f"Which one? {', '.join(c.business_name for c in found)}.")
        c = next((c for c in found if c.business_name.lower() == customer.strip().lower()), found[0])
        stmt, bal = _balances(db, user)
        balance = money(db.execute(stmt.where(Customer.id == c.id)).one()[2])
        due = func.coalesce(Invoice.due_date, Invoice.invoice_date)
        invs = db.execute(select(Invoice.number, Invoice.invoice_date, due, Invoice.total - Invoice.amount_paid)
                          .where(Invoice.customer_id == c.id, Invoice.status == InvoiceStatus.issued,
                                 Invoice.total > Invoice.amount_paid).order_by(due)).all()
        rows = [{"invoice": n, "invoice_date": d, "due_date": dd, "balance": money(b),
                 "days_overdue": max((today() - dd).days, 0)} for n, d, dd, b in invs]
        over = sum((r["balance"] for r in rows if r["days_overdue"] > 0), Decimal(0))
        text = (f"{c.business_name} owes {inr(balance)}" + (f", of which {inr(over)} is overdue" if over else "")
                + f" ({_plural(len(rows), 'unpaid invoice')}).") if balance > 0 else \
            (f"{c.business_name} has nothing outstanding" + (f" (advance {inr(-balance)})." if balance < 0 else "."))
        return _answer(text, [("invoice", "Invoice", "text"), ("invoice_date", "Date", "date"),
                              ("due_date", "Due", "date"), ("balance", "Balance", "money"),
                              ("days_overdue", "Days overdue", "int")], rows)
    stmt, bal = _balances(db, user)
    data = db.execute(_cust_scope(stmt.where(bal > 0), user).order_by(bal.desc())).all()
    rows = [{"customer_id": str(cid), "customer": name, "outstanding": money(b)} for cid, name, b in data]
    total = sum((r["outstanding"] for r in rows), Decimal(0))
    if not rows:
        return _answer("Nobody owes you anything right now.")
    top = rows[0]
    return _answer(f"Total outstanding is {inr(total)} across {_plural(len(rows), 'customer')}. "
                   f"Highest: {top['customer']} ({inr(top['outstanding'])}).",
                   [("customer", "Customer", "text"), ("outstanding", "Outstanding", "money")], rows)


def pending_approvals(db: Session, user: User) -> dict:
    stmt = (select(Order.id, Order.number, Customer.business_name, Order.grand_total, Order.hold_reasons, Order.created_at)
            .join(Customer, Customer.id == Order.customer_id).where(Order.status == OrderStatus.on_hold)
            .order_by(Order.created_at))
    rows = [{"order_id": str(oid), "order": num, "customer": name, "value": money(v), "reason": r or "",
             "since": ca.astimezone(IST).date()} for oid, num, name, v, r, ca in db.execute(_cust_scope(stmt, user)).all()]
    if not rows:
        return _answer("No orders are waiting for approval.")
    total = sum((r["value"] for r in rows), Decimal(0))
    return _answer(f"{_plural(len(rows), 'order')} worth {inr(total)} {'is' if len(rows) == 1 else 'are'} "
                   "waiting for approval (Owner/Admin).",
                   [("order", "Order", "text"), ("customer", "Customer", "text"), ("value", "Value", "money"),
                    ("reason", "On hold because", "text"), ("since", "Since", "date")], rows)


def unanswered_chats(db: Session, user: User) -> dict:
    stmt = (select(Conversation.id, Customer.business_name, Conversation.unread_count, Conversation.last_inbound_at,
                   Conversation.handover_reason, Conversation.last_message_preview)
            .join(Customer, Customer.id == Conversation.customer_id)
            .where(Conversation.tenant_id == user.tenant_id,
                   or_(Conversation.unread_count > 0, Conversation.handover_at.is_not(None)))
            .order_by(Conversation.last_inbound_at.asc().nulls_last()))
    rows = [{"conversation_id": str(cid), "customer": name, "unread": n,
             "waiting_since": li.astimezone(IST).replace(tzinfo=None) if li else None,
             "needs_person": reason or "", "last_message": (prev or "")[:80]}
            for cid, name, n, li, reason, prev in db.execute(_cust_scope(stmt, user)).all()]
    if not rows:
        return _answer("No chats are waiting for a reply.")
    people = sum(1 for r in rows if r["needs_person"])
    return _answer(f"{_plural(len(rows), 'chat')} waiting for a reply" + (f", {people} handed over by the bot." if people else "."),
                   [("customer", "Customer", "text"), ("unread", "Unread", "int"), ("waiting_since", "Since", "datetime"),
                    ("needs_person", "Needs a person", "text"), ("last_message", "Last message", "text")], rows)


def invoices_due(db: Session, user: User, p: Period) -> dict:
    due = func.coalesce(Invoice.due_date, Invoice.invoice_date)
    stmt = (select(Invoice.number, Customer.business_name, due, Invoice.total - Invoice.amount_paid)
            .join(Customer, Customer.id == Invoice.customer_id)
            .where(Invoice.status == InvoiceStatus.issued, Invoice.total > Invoice.amount_paid, due >= p.start, due <= p.end)
            .order_by(due, Customer.business_name))
    rows = [{"invoice": n, "customer": c, "due_date": d, "balance": money(b)} for n, c, d, b in
            db.execute(_cust_scope(stmt, user)).all()]
    if not rows:
        return _answer(f"No unpaid invoices fall due {p.label}.")
    total = sum((r["balance"] for r in rows), Decimal(0))
    return _answer(f"{_plural(len(rows), 'invoice')} worth {inr(total)} {'falls' if len(rows) == 1 else 'fall'} due {p.label}.",
                   [("invoice", "Invoice", "text"), ("customer", "Customer", "text"), ("due_date", "Due", "date"),
                    ("balance", "Balance", "money")], rows)


def stock_alerts(db: Session, user: User) -> dict:
    stmt = (select(Product.sku, Product.name, Product.stock_status).where(
        Product.tenant_id == user.tenant_id, Product.is_active.is_(True),
        Product.stock_status.in_([StockStatus.low, StockStatus.out_of_stock]))
        .order_by(case((Product.stock_status == StockStatus.out_of_stock, 0), else_=1), Product.name))
    rows = [{"sku": s, "product": n, "stock": "Out of stock" if st == StockStatus.out_of_stock else "Low"}
            for s, n, st in db.execute(stmt).all()]
    if not rows:
        return _answer("All active products are in stock.")
    out = sum(1 for r in rows if r["stock"] == "Out of stock")
    return _answer(f"{_plural(out, 'product')} out of stock and {len(rows) - out} running low.",
                   [("sku", "SKU", "text"), ("product", "Product", "text"), ("stock", "Stock", "text")], rows)


def customer_orders(db: Session, user: User, customer: str, p: Period) -> dict:
    found = find_customers(db, user, customer)
    if not found:
        return _answer(f"I couldn't find a customer called \"{customer}\".")
    c = found[0]
    stmt = (select(Order.number, Order.created_at, Order.status, Order.source, Order.grand_total)
            .where(Order.tenant_id == user.tenant_id, Order.customer_id == c.id,
                   Order.created_at >= p.start_dt, Order.created_at < p.end_dt).order_by(Order.created_at.desc()))
    rows = [{"order": n, "date": ca.astimezone(IST).date(), "status": s.value.replace("_", " "),
             "source": SOURCE_LABEL[src], "value": money(v)} for n, ca, s, src, v in db.execute(stmt).all()]
    placed = [r for r in rows if r["status"] in {s.value.replace("_", " ") for s in PLACED}]
    if not rows:
        return _answer(f"{c.business_name} has no orders {p.label}.")
    value = sum((r["value"] for r in placed), Decimal(0))
    return _answer(f"{c.business_name}: {_plural(len(placed), 'order')} placed {p.label}, worth {inr(value)}"
                   + (f" (plus {len(rows) - len(placed)} quotes/drafts/cancelled)." if len(rows) > len(placed) else "."),
                   [("order", "Order", "text"), ("date", "Date", "date"), ("status", "Status", "text"),
                    ("source", "Source", "text"), ("value", "Value", "money")], rows)


# ---------------------------------------------------------------- daily summary (FR-M10-08)
def daily_figures(db: Session, tenant_id: uuid.UUID, on: date) -> dict:
    """Figures for the day before `on`, for the whole company."""
    owner = User(tenant_id=tenant_id, role=Role.owner)  # transient: company-wide scope
    y = on - timedelta(days=1)
    p = Period(y, y, "yesterday")
    s = sales_summary(db, owner, p)
    orders_n = sum(r["orders"] for r in s["rows"]) if s["rows"] else 0
    orders_v = sum((r["value"] for r in s["rows"]), Decimal(0)) if s["rows"] else Decimal(0)
    c = collections_summary(db, owner, p)
    coll = sum((r["amount"] for r in c["rows"]), Decimal(0))
    due = func.coalesce(Invoice.due_date, Invoice.invoice_date)
    new_over = db.execute(select(func.count(Invoice.id), func.coalesce(func.sum(Invoice.total - Invoice.amount_paid), 0))
                          .where(Invoice.tenant_id == tenant_id, Invoice.status == InvoiceStatus.issued,
                                 Invoice.total > Invoice.amount_paid, due == y)).one()
    total_over = db.scalar(select(func.coalesce(func.sum(Invoice.total - Invoice.amount_paid), 0)).where(
        Invoice.tenant_id == tenant_id, Invoice.status == InvoiceStatus.issued, Invoice.total > Invoice.amount_paid,
        due < on))
    approvals = db.scalar(select(func.count(Order.id)).where(Order.tenant_id == tenant_id,
                                                             Order.status == OrderStatus.on_hold)) or 0
    chats = db.scalar(select(func.count(Conversation.id)).where(
        Conversation.tenant_id == tenant_id, or_(Conversation.unread_count > 0, Conversation.handover_at.is_not(None)))) or 0
    return {
        "date": y.isoformat(), "orders": orders_n, "order_value": str(money(orders_v)),
        "whatsapp_orders": next((r["orders"] for r in s["rows"] if r["source"] == SOURCE_LABEL[OrderSource.whatsapp]), 0)
        if s["rows"] else 0,
        "collections": str(money(coll)), "new_overdue_invoices": new_over[0], "new_overdue_amount": str(money(new_over[1])),
        "total_overdue": str(money(total_over)), "pending_approvals": approvals, "unanswered_chats": chats,
    }


def daily_text(company: str, f: dict) -> str:
    y = date.fromisoformat(f["date"])
    lines = [
        f"Good morning! {company} – summary for {y:%a %d %b}:",
        f"• Orders: {f['orders']} worth {inr(f['order_value'])}" + (f" ({f['whatsapp_orders']} via WhatsApp AI)" if f["whatsapp_orders"] else ""),
        f"• Collections: {inr(f['collections'])}",
        f"• Newly overdue: {f['new_overdue_invoices']} invoice(s), {inr(f['new_overdue_amount'])} (total overdue {inr(f['total_overdue'])})",
        f"• Waiting for your approval: {f['pending_approvals']} order(s)",
        f"• Chats waiting for a reply: {f['unanswered_chats']}",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------- dashboard (FR-M10-10)
def dashboard(db: Session, user: User, p: Period) -> dict:
    s = sales_summary(db, user, p)
    c = collections_summary(db, user, p)
    stmt, bal = _balances(db, user)
    owing = _cust_scope(stmt.where(bal > 0), user).subquery()
    outstanding_total = db.scalar(select(func.coalesce(func.sum(owing.c.balance), 0))) or 0
    over = overdue_customers(db, user, 0)
    # daily series
    day = func.date(func.timezone("Asia/Kolkata", Order.created_at))
    ostmt = (select(day, func.count(Order.id), func.sum(Order.grand_total)).join(Customer, Customer.id == Order.customer_id)
             .where(Order.status.in_(PLACED), Order.created_at >= p.start_dt, Order.created_at < p.end_dt).group_by(day))
    by_day = {d: (n, money(v)) for d, n, v in db.execute(_cust_scope(ostmt, user)).all()}
    pstmt = (select(Payment.payment_date, func.sum(Payment.amount)).join(Customer, Customer.id == Payment.customer_id)
             .where(Payment.status == PaymentStatus.confirmed, Payment.payment_date >= p.start,
                    Payment.payment_date <= p.end).group_by(Payment.payment_date))
    paid = {d: money(v) for d, v in db.execute(_cust_scope(pstmt, user)).all()}
    series = []
    d = p.start
    while d <= p.end and len(series) < 400:
        n, v = by_day.get(d, (0, Decimal(0)))
        series.append({"date": d.isoformat(), "orders": n, "order_value": str(v), "collections": str(paid.get(d, Decimal(0)))})
        d += timedelta(days=1)
    # AI match rate: WhatsApp orders in the period, lines the AI matched vs items it had to ask about
    wa = db.execute(_cust_scope(
        select(Order.id, Order.ai_unclear).join(Customer, Customer.id == Order.customer_id)
        .where(Order.source == OrderSource.whatsapp, Order.created_at >= p.start_dt, Order.created_at < p.end_dt), user)).all()
    matched = db.scalar(select(func.count(OrderLine.id)).where(OrderLine.order_id.in_([o for o, _ in wa]))) if wa else 0
    unclear = sum(len(u or []) for _, u in wa)
    rows_src = s["rows"] or [{"source": SOURCE_LABEL[x], "orders": 0, "value": Decimal(0)} for x in OrderSource]
    count = sum(r["orders"] for r in rows_src)
    value = sum((money(r["value"]) for r in rows_src), Decimal(0))
    return {
        "period": {"start": p.start.isoformat(), "end": p.end.isoformat(), "label": p.label},
        "orders": {"count": count, "value": str(value), "average": str(money(value / count) if count else Decimal(0)),
                   "by_source": [{**r, "value": str(r["value"])} for r in rows_src]},
        "collections": {"amount": str(sum((r["amount"] for r in c["rows"]), Decimal(0))),
                        "count": sum(r["payments"] for r in c["rows"]),
                        "by_mode": [{**r, "amount": str(r["amount"])} for r in c["rows"]]},
        "outstanding": {"total": str(money(outstanding_total)),
                        "overdue": str(sum((r["overdue_amount"] for r in over["rows"]), Decimal(0))),
                        "overdue_customers": len(over["rows"])},
        "top_customers": [{**r, "order_value": str(r["order_value"])} for r in top_customers(db, user, p, 5)["rows"]],
        "top_products": [{**r, "quantity": str(r["quantity"]), "value": str(r["value"])}
                         for r in top_products(db, user, p, 5)["rows"]],
        "ai": {"whatsapp_orders": len(wa), "lines_matched": matched, "items_asked": unclear,
               "match_rate": round(matched / (matched + unclear), 3) if (matched + unclear) else None},
        "series": series,
    }
