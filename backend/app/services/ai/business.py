"""Business assistant for seller staff (FR-M10-06, 07, 09).

The question is turned into ONE call of a fixed analytics function (services/analytics.py) with parameters - by a
rule-based router with the development AI, or by the LLM choosing a tool with a real provider. The answer text and
table come from that function, so every figure is from the database. "Remind everyone above 60 days every Monday"
becomes a rule PROPOSAL that the owner must confirm.
"""

import re
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy.orm import Session

from app.models import Role, User
from app.services import analytics as an
from app.services.ai.providers import AIError, JsonResult, get_provider

WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]

TOOLS = {
    "overdue_customers": "Customers with invoices more than min_days days overdue (0 = any overdue)",
    "top_customers": "Customers ranked by order value in a period (limit = how many)",
    "inactive_customers": "Customers with no order in the last `days` days",
    "top_products": "Best-selling products by value in a period",
    "sales_summary": "Number and value of orders in a period, split by source (WhatsApp AI / panel / Excel)",
    "collections_summary": "Payments received in a period, by mode",
    "outstanding": "Total receivable and who owes most, or one customer's balance (customer = name)",
    "pending_approvals": "Orders on hold waiting for Owner/Admin approval",
    "unanswered_chats": "WhatsApp chats waiting for a reply, including ones the bot handed over",
    "invoices_due": "Unpaid invoices falling due in a period (today, this week, next 7 days...)",
    "stock_alerts": "Products out of stock or running low",
    "customer_orders": "One customer's orders in a period (customer = name)",
    "create_reminder_rule": "The owner asks for a recurring payment reminder, e.g. remind everyone above 60 days "
                            "overdue every Monday (min_days, weekdays, hour)",
    "none": "Not a question about this business's data",
}


@dataclass
class Plan:
    tool: str
    params: dict = field(default_factory=dict)
    provider: str = "rules"
    raw: JsonResult | None = None


# ---------------------------------------------------------------- rule-based router (mock provider)
def _int(rx: str, text: str, default: int | None = None) -> int | None:
    m = re.search(rx, text, re.I)
    return int(m.group(1)) if m else default


def _customer_name(text: str) -> str | None:
    m = re.search(r"\b(?:of|for|from|by|does|did|is|has)\s+([A-Z][\w&.'-]*(?:\s+[A-Z][\w&.'-]*){0,4})", text)
    if m:
        name = m.group(1).strip()
        if name.lower() not in {"me", "us", "all", "each", "every", "today", "this", "last", "the", "january",
                                "february", "march", "april", "may", "june", "july", "august", "september",
                                "october", "november", "december"}:
            return name
    m = re.search(r"([A-Z][\w&.'-]*(?:\s+[A-Z][\w&.'-]*){0,4})(?:'s|s')\s+(?:outstanding|balance|dues|orders?)", text)
    return m.group(1) if m else None


def rule_plan(question: str) -> Plan:
    q = question.strip()
    t = q.lower()
    days_rx = r"(\d{1,4})\s*\+?\s*days?"
    if re.search(r"\b(remind|reminder)\b", t) and re.search(r"\bevery\b|\bdaily\b|\bweekly\b", t):
        wds = [i for i, w in enumerate(WEEKDAYS) if re.search(rf"\b{w}s?\b|\b{w[:3]}\b", t)]
        if re.search(r"\bdaily\b|every\s*day\b", t):
            wds = list(range(7))
        hour = _int(r"\bat\s+(\d{1,2})\s*(?:am|a\.m\.)?\b", t, 10)
        if re.search(r"\bat\s+\d{1,2}\s*(pm|p\.m\.)", t) and hour < 12:
            hour += 12
        return Plan("create_reminder_rule", {"min_days": _int(days_rx, t, 0), "weekdays": wds or [0], "hour": hour})
    if re.search(r"overdue|over\s*due|past\s+due|late\s+pay", t) and not re.search(r"\bdue\s+(today|this|next|tomorrow)", t):
        return Plan("overdue_customers", {"min_days": _int(r"(?:above|over|more\s+than|beyond|>)\s*" + days_rx, t)
                                          or _int(days_rx, t, 0)})
    if re.search(r"no\s+orders?|not\s+(ordered|placed)|haven'?t\s+ordered|didn'?t\s+order|inactive|dormant|stopped\s+ordering", t):
        return Plan("inactive_customers", {"days": _int(days_rx, t, 30)})
    if re.search(r"\b(top|best|biggest|highest|largest)\b.*\b(products?|items?|skus?|selling)\b|\bbest\s*sell|\bfast\s+moving", t):
        return Plan("top_products", {"period": t, "limit": _int(r"\btop\s+(\d{1,3})", t, 10)})
    if re.search(r"\b(top|best|biggest|highest|largest)\b.*\b(customers?|buyers?|retailers?|dealers?|distributors?)\b", t):
        return Plan("top_customers", {"period": t, "limit": _int(r"\btop\s+(\d{1,3})", t, 10)})
    if re.search(r"out\s+of\s+stock|low\s+stock|stock\s+(alert|status)|running\s+low", t):
        return Plan("stock_alerts")
    if re.search(r"approv|on\s+hold|\bhold\b", t):
        return Plan("pending_approvals")
    if re.search(r"unanswered|unread|chats?|waiting\s+for\s+(a\s+)?reply|not\s+replied|messages\s+pending|handed\s+over", t):
        return Plan("unanswered_chats")
    if re.search(r"\bdue\s+(today|tomorrow|this\s+week|next\s+week|next\s+7|in\s+the\s+next)|falling\s+due|invoices?\s+due", t):
        key = "today" if "today" in t else "next_7_days"
        return Plan("invoices_due", {"period": key})
    if re.search(r"collect|received|receipts?|payments?\s+(came|in|received)|money\s+came|how\s+much\s+(did\s+we\s+)?(get|receive)", t):
        return Plan("collections_summary", {"period": t})
    if re.search(r"outstanding|receivable|owe|owing|balance|dues\b|pending\s+amount", t):
        return Plan("outstanding", {"customer": _customer_name(q)})
    if re.search(r"\borders?\b", t) and (name := _customer_name(q)) and not re.search(r"\bhow\s+many\s+orders\b", t):
        return Plan("customer_orders", {"customer": name, "period": t})
    if re.search(r"\b(sales|orders?|order\s+value|turnover|revenue|business)\b", t):
        return Plan("sales_summary", {"period": t})
    return Plan("none")


# ---------------------------------------------------------------- LLM router
SCHEMA = {
    "type": "object",
    "properties": {
        "tool": {"type": "string", "enum": list(TOOLS)},
        "period": {"type": ["string", "null"], "enum": [*an.PERIOD_KEYS, *[f"month_{i}" for i in range(1, 13)], None],
                   "description": "Time period; null for the tool's default"},
        "days": {"type": ["integer", "null"], "description": "For inactive_customers, or a custom 'last N days'"},
        "min_days": {"type": ["integer", "null"], "description": "Overdue threshold in days"},
        "limit": {"type": ["integer", "null"]},
        "customer": {"type": ["string", "null"], "description": "Customer name as the user wrote it"},
        "weekdays": {"type": ["array", "null"], "items": {"type": "integer"}, "description": "0 = Monday ... 6 = Sunday"},
        "hour": {"type": ["integer", "null"], "description": "Hour of day, 24h, IST"},
    },
    "required": ["tool", "period", "days", "min_days", "limit", "customer", "weekdays", "hour"],
}


def llm_plan(question: str, on: date) -> Plan:
    provider = get_provider()
    system = ("You route questions from a B2B seller's staff to ONE reporting tool. Never answer the question yourself "
              f"and never invent numbers. Today is {on:%A %d %B %Y} (India). Tools:\n"
              + "\n".join(f"- {k}: {v}" for k, v in TOOLS.items()))
    res = provider.extract_json(system, question, SCHEMA, fast=True)
    d = res.data or {}
    tool = d.get("tool") if d.get("tool") in TOOLS else "none"
    params: dict = {}
    if tool in ("top_customers", "top_products", "sales_summary", "collections_summary", "invoices_due", "customer_orders"):
        params["period"] = d.get("period") or ("next_7_days" if tool == "invoices_due" else "this_month")
        if d.get("days") and not d.get("period"):
            params["period"] = f"last_{int(d['days'])}_days"
    if tool in ("top_customers", "top_products"):
        params["limit"] = d.get("limit") or 10
    if tool == "inactive_customers":
        params["days"] = d.get("days") or 30
    if tool == "overdue_customers":
        params["min_days"] = d.get("min_days") or 0
    if tool in ("outstanding", "customer_orders"):
        params["customer"] = d.get("customer")
    if tool == "create_reminder_rule":
        params = {"min_days": d.get("min_days") or 0, "weekdays": d.get("weekdays") or [0], "hour": d.get("hour") or 10}
    return Plan(tool, params, provider.name, res)


def plan_for(question: str) -> Plan:
    provider = get_provider()
    if provider.name == "mock" or not hasattr(provider, "extract_json"):
        return rule_plan(question)
    try:
        return llm_plan(question, an.today())
    except AIError:
        p = rule_plan(question)
        p.provider = "rules-fallback"
        return p


# ---------------------------------------------------------------- execution
def _period(value) -> an.Period:
    if not value:
        return an.period("this_month")
    v = str(value)
    if v in an.PERIOD_KEYS or re.fullmatch(r"(month_\d{1,2}|last_\d+_days)", v):
        return an.period(v)
    return an.period_from_text(v)


def describe_rule(min_days: int, weekdays: list[int], hour: int) -> str:
    days = "every day" if sorted(weekdays) == list(range(7)) else "every " + ", ".join(
        WEEKDAYS[d].title() for d in sorted(set(weekdays)))
    who = f"more than {min_days} days overdue" if min_days else "with overdue invoices"
    h = f"{hour % 12 or 12} {'AM' if hour < 12 else 'PM'}"
    return f"Send a WhatsApp payment reminder to every customer {who}, {days} at {h} IST."


def run(db: Session, user: User, plan: Plan) -> dict:
    """Returns {"kind", "text", "columns", "rows", "total_rows", "rule"?}."""
    p = plan.params
    tool = plan.tool
    if tool == "create_reminder_rule":
        min_days = max(int(p.get("min_days") or 0), 0)
        wds = sorted({int(x) for x in (p.get("weekdays") or [0]) if 0 <= int(x) <= 6}) or [0]
        hour = min(max(int(p.get("hour") or 10), 0), 23)
        desc = describe_rule(min_days, wds, hour)
        if user.role != Role.owner:
            return {"kind": "answer", "text": "Only the Owner can set up automatic reminders. " + desc,
                    "columns": [], "rows": [], "total_rows": 0}
        rule = {"kind": "overdue_reminder", "params": {"min_days": min_days}, "weekdays": wds, "hour": hour,
                "description": desc}
        return {"kind": "rule_proposal", "text": f"I'll set this up: {desc} Confirm to create it.", "columns": [],
                "rows": [], "total_rows": 0, "rule": rule}
    funcs = {
        "overdue_customers": lambda: an.overdue_customers(db, user, int(p.get("min_days") or 0)),
        "top_customers": lambda: an.top_customers(db, user, _period(p.get("period")), int(p.get("limit") or 10)),
        "inactive_customers": lambda: an.inactive_customers(db, user, int(p.get("days") or 30)),
        "top_products": lambda: an.top_products(db, user, _period(p.get("period")), int(p.get("limit") or 10)),
        "sales_summary": lambda: an.sales_summary(db, user, _period(p.get("period"))),
        "collections_summary": lambda: an.collections_summary(db, user, _period(p.get("period"))),
        "outstanding": lambda: an.outstanding(db, user, p.get("customer")),
        "pending_approvals": lambda: an.pending_approvals(db, user),
        "unanswered_chats": lambda: an.unanswered_chats(db, user),
        "invoices_due": lambda: an.invoices_due(db, user, _period(p.get("period") or "next_7_days")),
        "stock_alerts": lambda: an.stock_alerts(db, user),
        "customer_orders": lambda: an.customer_orders(db, user, p.get("customer") or "", _period(p.get("period"))),
    }
    if tool not in funcs:
        return {"kind": "unknown", "columns": [], "rows": [], "total_rows": 0,
                "text": "I can answer questions about your orders, customers, payments, invoices, stock and chats, "
                        "e.g. \"who is above 60 days overdue?\", \"top 10 customers this month\", \"customers with no "
                        "order in 30 days\". I couldn't match this question to your data, so I won't guess."}
    out = funcs[tool]()
    return {"kind": "answer", **out}
