"""Read a buyer's payment proof (FR-M8-07): amount, date, UTR / cheque number, payer, mode.

Photos/screenshots need a real AI provider. Typed messages ("paid 5000 by gpay, UTR 412345678901") are also
parsed by rules, so the flow works with the development AI too. The result is only a proposal: Accounts confirm it.
"""

import re
from decimal import Decimal, InvalidOperation

from app.services.ai.invoice_reader import parse_date
from app.services.ai.providers import AIError, JsonResult, get_provider

SYSTEM = """You read payment proofs that Indian shop owners send their supplier on WhatsApp: UPI/GPay/PhonePe/Paytm
screenshots, NEFT/RTGS/IMPS bank confirmations, cheque photos, or a typed message.
Extract: amount in rupees (number), payment_date (YYYY-MM-DD), reference (UPI ref / UTR / transaction id, or the
cheque number for a cheque), payer_name, mode (upi, neft, rtgs, cheque, cash, other), bank (for cheques).
Use null for anything you cannot see. Never guess numbers."""

SCHEMA = {
    "type": "object",
    "properties": {
        "is_payment_proof": {"type": "boolean"},
        "amount": {"type": ["number", "null"]},
        "payment_date": {"type": ["string", "null"]},
        "reference": {"type": ["string", "null"]},
        "payer_name": {"type": ["string", "null"]},
        "mode": {"type": ["string", "null"], "enum": ["upi", "neft", "rtgs", "cheque", "cash", "other", None]},
        "bank": {"type": ["string", "null"]},
    },
    "required": ["is_payment_proof", "amount", "payment_date", "reference", "payer_name", "mode", "bank"],
}


def _amount(v) -> Decimal | None:
    if v in (None, ""):
        return None
    try:
        a = Decimal(str(v).replace(",", "").replace("₹", "").strip())
        return a if a > 0 else None
    except InvalidOperation:
        return None


def rule_read(text: str) -> dict:
    t = text or ""
    out: dict = {"amount": None, "reference": None, "mode": None, "payment_date": None, "payer_name": None}
    if m := re.search(r"(?:utr|ref(?:erence)?|txn|transaction(?:\s*id)?)\s*(?:no\.?|number|id)?\s*[:#\-]?\s*([A-Z0-9]{8,22})", t, re.I):
        out["reference"] = m.group(1)
    elif m := re.search(r"(?:cheque|chq|check)\s*(?:no\.?|number)?\s*[:#\-]?\s*(\d{6})\b", t, re.I):
        out["reference"] = m.group(1)
    elif m := re.search(r"\b(\d{12})\b", t):  # UPI references are 12 digits
        out["reference"] = m.group(1)
    if re.search(r"\b(cheque|chq|check)\b", t, re.I):
        out["mode"] = "cheque"
    elif re.search(r"\bneft\b", t, re.I):
        out["mode"] = "neft"
    elif re.search(r"\brtgs\b", t, re.I):
        out["mode"] = "rtgs"
    elif re.search(r"\b(upi|gpay|google\s*pay|phonepe|paytm|bhim|imps)\b", t, re.I):
        out["mode"] = "upi"
    elif re.search(r"\bcash\b", t, re.I):
        out["mode"] = "cash"
    m = re.search(r"(?:₹|rs\.?|inr)\s*([\d,]+(?:\.\d{1,2})?)", t, re.I) or \
        re.search(r"(?:paid|sent|transferred|deposited|amount)\D{0,12}([\d,]+(?:\.\d{1,2})?)", t, re.I)
    if m and (a := _amount(m.group(1))) and a < 10**9 and m.group(1).replace(",", "") != (out["reference"] or ""):
        out["amount"] = a
    if m := re.search(r"\b(\d{1,2}[-/.]\d{1,2}[-/.]\d{2,4})\b", t):
        out["payment_date"] = parse_date(m.group(1))
    return out


def read_payment(text: str | None, image: tuple[bytes, str] | None = None) -> tuple[dict, JsonResult | None]:
    provider = get_provider()
    if image is None:
        fields = rule_read(text or "")
        return fields, None
    if provider.name == "mock":
        raise AIError("The development AI can't read payment screenshots. Set a real AI provider.")
    res = provider.extract_json(SYSTEM, f"Buyer's message: {text or '(photo only)'}", SCHEMA, image=image)
    d = res.data
    if d.get("is_payment_proof") is False:
        return {}, res
    return {"amount": _amount(d.get("amount")), "reference": d.get("reference"), "mode": d.get("mode"),
            "payment_date": parse_date(d.get("payment_date")), "payer_name": d.get("payer_name"),
            "bank": d.get("bank")}, res
