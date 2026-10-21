"""Read an uploaded invoice PDF (FR-M7-01): number, date, customer, amounts, due date.

Real providers read the PDF (text, or the page itself for scanned files). The mock provider parses the PDF's text
layer with rules, which works for most ERP/Tally-generated PDFs but not for scans.
Staff always review and confirm before the invoice is saved.
"""

import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from io import BytesIO

from pypdf import PdfReader

from app.services.ai.providers import AIError, JsonResult, get_provider

SYSTEM = """You read Indian GST tax invoices issued by a seller to their buyer and extract the key fields.
Dates as YYYY-MM-DD. Amounts as plain numbers in rupees. customer_* is the BUYER (bill to), never the seller.
tax_amount = total CGST + SGST + IGST. Use null for anything not printed on the invoice."""

SCHEMA = {
    "type": "object",
    "properties": {
        "invoice_number": {"type": ["string", "null"]},
        "invoice_date": {"type": ["string", "null"]},
        "due_date": {"type": ["string", "null"]},
        "customer_name": {"type": ["string", "null"]},
        "customer_gstin": {"type": ["string", "null"]},
        "taxable_value": {"type": ["number", "null"]},
        "tax_amount": {"type": ["number", "null"]},
        "total": {"type": ["number", "null"]},
    },
    "required": ["invoice_number", "invoice_date", "due_date", "customer_name", "customer_gstin", "taxable_value",
                 "tax_amount", "total"],
}
FIELDS = list(SCHEMA["properties"])
GSTIN_RX = re.compile(r"\b\d{2}[A-Z]{5}\d{4}[A-Z][A-Z0-9]Z[A-Z0-9]\b")
DATE_RX = r"(\d{1,2}[-/.]\d{1,2}[-/.]\d{2,4}|\d{1,2}[- ][A-Za-z]{3,9}[- ,]+\d{4}|\d{4}-\d{2}-\d{2})"
AMOUNT_RX = r"(?:rs\.?|inr|₹)?\s*([\d,]+(?:\.\d{1,2})?)"


def pdf_text(content: bytes, max_pages: int = 4) -> str:
    try:
        reader = PdfReader(BytesIO(content))
        return "\n".join((p.extract_text() or "") for p in reader.pages[:max_pages]).strip()
    except Exception as exc:  # noqa: BLE001 - any parse failure means unreadable file
        raise AIError("This file is not a readable PDF") from exc


def parse_date(v) -> date | None:
    if not v:
        return None
    if isinstance(v, date):
        return v
    s = str(v).strip()
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%d.%m.%Y", "%d-%m-%y", "%d/%m/%y", "%d %b %Y", "%d-%b-%Y",
                "%d %B %Y", "%d-%B-%Y", "%d %b, %Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def parse_amount(v) -> Decimal | None:
    if v is None or v == "":
        return None
    try:
        return Decimal(str(v).replace(",", "").replace("₹", "").replace("Rs.", "").strip()).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        return None


def _last_amount(label: str, text: str) -> Decimal | None:
    hits = re.findall(label + r"[^\n\d]{0,30}?" + AMOUNT_RX, text, re.IGNORECASE)
    return parse_amount(hits[-1]) if hits else None


def rule_read(text: str, seller_gstin: str | None) -> dict:
    out: dict = dict.fromkeys(FIELDS)
    if m := re.search(r"(?:invoice|bill|inv)\s*(?:no|number|#)\.?\s*[:\-]?\s*([A-Z0-9][A-Z0-9/\-]{1,30})", text, re.I):
        out["invoice_number"] = m.group(1).strip()
    if m := re.search(r"(?<!due )(?:invoice\s+)?date[d]?\s*[:\-]?\s*" + DATE_RX, text, re.I):
        out["invoice_date"] = m.group(1)
    if m := re.search(r"due\s*date\s*[:\-]?\s*" + DATE_RX, text, re.I):
        out["due_date"] = m.group(1)
    gstins = [g for g in GSTIN_RX.findall(text.upper()) if g != (seller_gstin or "").upper()]
    if gstins:
        out["customer_gstin"] = gstins[0]
    if m := re.search(r"(?:bill(?:ed)?\s*to|buyer|customer)\s*[:\-]?\s*\n?\s*([^\n]{3,80})", text, re.I):
        name = re.split(r"\s{2,}|ship to", m.group(1), flags=re.I)[0].strip(" :-")
        out["customer_name"] = name or None
    out["total"] = (_last_amount(r"(?:grand\s+total|invoice\s+total|total\s+amount|total\s*\(rs\.?\)|net\s+amount)", text)
                    or _last_amount(r"\btotal\b", text))
    out["taxable_value"] = _last_amount(r"taxable\s+(?:value|amount)", text)
    taxes = [a for a in (_last_amount(r"\bcgst\b", text), _last_amount(r"\bsgst\b", text),
                         _last_amount(r"\bigst\b", text)) if a]
    if taxes:
        out["tax_amount"] = sum(taxes, Decimal(0))
    return out


def read_invoice(content: bytes, seller_gstin: str | None) -> tuple[dict, JsonResult | None, str]:
    """Returns (fields, AI result or None for rules, provider name)."""
    text = pdf_text(content)
    provider = get_provider()
    if provider.name == "mock":
        if not text:
            raise AIError("This PDF has no text layer (looks scanned). The development AI can't read it; "
                          "fill the fields by hand or switch to a real AI provider.")
        return rule_read(text, seller_gstin), None, "mock"
    prompt = f"Our (seller's) GSTIN: {seller_gstin or 'unknown'}.\n\n" + (
        f"Invoice text:\n{text[:12000]}" if text else "The invoice is attached as a PDF.")
    res = provider.extract_json(SYSTEM, prompt, SCHEMA, pdf=None if text else content)
    return {k: res.data.get(k) for k in FIELDS}, res, provider.name
