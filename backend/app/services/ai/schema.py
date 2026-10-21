"""What we ask the AI for, and the shape it must answer in.

The AI only reads the buyer's message. It never sets prices or confirms orders: matching is checked against
our catalog in matcher.py and prices always come from the pricing engine (M5 business rules).
"""

from typing import Literal

from pydantic import BaseModel, Field, field_validator

Intent = Literal[
    "order", "order_change", "order_status", "price_enquiry", "outstanding", "ledger", "invoice_copy", "payment",
    "complaint", "human", "greeting", "other"
]
INTENTS = ["order", "order_change", "order_status", "price_enquiry", "outstanding", "ledger", "invoice_copy",
           "payment", "complaint", "human", "greeting", "other"]


class ExtractedLine(BaseModel):
    text: str = Field(description="The item exactly as the buyer wrote it, translated to English if needed")
    quantity: float | None = None
    unit: str | None = None
    sku: str | None = Field(default=None, description="SKU from the catalog if you are sure which product it is")
    confidence: float = Field(default=0, ge=0, le=1)

    @field_validator("quantity", mode="before")
    @classmethod
    def _qty(cls, v):
        if v in ("", None):
            return None
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    @field_validator("confidence", mode="before")
    @classmethod
    def _conf(cls, v):
        try:
            return min(max(float(v), 0), 1)
        except (TypeError, ValueError):
            return 0


class Change(BaseModel):
    action: Literal["set_quantity", "remove", "add"]
    item_number: int | None = None
    text: str | None = None
    quantity: float | None = None
    sku: str | None = None


class Extraction(BaseModel):
    intent: Intent = "other"
    language: str | None = None
    lines: list[ExtractedLine] = []
    changes: list[Change] = []
    reference: str | None = None  # invoice number for invoice_copy
    other_party: bool = False     # asks about someone else's account (M10: refused)

    @field_validator("other_party", mode="before")
    @classmethod
    def _bool(cls, v):
        return bool(v)

    @field_validator("intent", mode="before")
    @classmethod
    def _intent(cls, v):
        return v if v in INTENTS else "other"


JSON_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "intent": {"type": "string", "enum": INTENTS},
        "language": {"type": "string", "description": "Language of the message, e.g. en, ta, hi, tanglish"},
        "reference": {"type": ["string", "null"], "description": "Invoice number the buyer asks for, if any"},
        "other_party": {"type": "boolean",
                        "description": "true if the buyer asks about another business's orders, dues or invoices"},
        "lines": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "quantity": {"type": ["number", "null"]},
                    "unit": {"type": ["string", "null"]},
                    "sku": {"type": ["string", "null"]},
                    "confidence": {"type": "number"},
                },
                "required": ["text", "quantity", "unit", "sku", "confidence"],
            },
        },
        "changes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["set_quantity", "remove", "add"]},
                    "item_number": {"type": ["integer", "null"]},
                    "text": {"type": ["string", "null"]},
                    "quantity": {"type": ["number", "null"]},
                    "sku": {"type": ["string", "null"]},
                },
                "required": ["action", "item_number", "text", "quantity", "sku"],
            },
        },
    },
    "required": ["intent", "language", "lines", "changes", "other_party"],
}

SYSTEM_PROMPT = """You read WhatsApp messages that shop owners in India send to their supplier, and turn them into data.

Messages may be in English, Tamil, Hindi or a mix (Tanglish, Hinglish), typed or as a photo of a handwritten or
printed list, and may write numbers as words ("pathu" = 10, "das" = 10, "dozen" = 12).

Decide the intent:
- order: the buyer lists items they want (with or without quantities)
- order_change: the buyer changes the open quote shown below ("make salt 30", "remove item 2", "add 5 soap")
- order_status: asks where their order is / delivery status
- price_enquiry: asks the rate or price of items, without ordering
- outstanding: asks how much they owe, their balance, or which invoices are due
- ledger: asks for a statement / ledger / account copy document (keep any month they mention in the text)
- invoice_copy: asks for a copy of an invoice or bill; put the invoice number in "reference" if given
- payment: says they paid, or sends payment proof / UTR
- complaint: damaged, wrong or missing goods, delays
- human: wants to talk to a person, asks for a call back or for the salesman / manager
- greeting: only a greeting like hi / vanakkam / namaste
- other: anything else

For order and price_enquiry, put each item in "lines": text = the item as written (translate to English),
quantity as a number (null if not given), unit (bag, box, kg, pcs...) if given.
If you are sure which catalog product it is, set "sku" to its SKU and "confidence" between 0 and 1 for how sure you
are. If two products could fit, leave sku null. Never invent SKUs that are not in the catalog.
For order_change, describe each change in "changes": item_number refers to the numbered lines of the open quote.
Never include prices; the system calculates them.
Set "other_party" to true when the buyer asks about another shop's or person's orders, dues or invoices
(e.g. "what is Kumar Stores' outstanding?"). Their own account is never other_party.
"language": en, ta (Tamil script), hi (Devanagari), tanglish or hinglish."""


def build_context(catalog: list[str], open_quote: list[str] | None) -> str:
    parts = ["Catalog (SKU | name | other names | unit):", *catalog]
    if open_quote:
        parts += ["", "Open quote the buyer may be changing:", *open_quote]
    return "\n".join(parts)
