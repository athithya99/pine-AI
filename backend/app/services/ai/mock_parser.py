"""Rule-based stand-in for the LLM ("mock" provider).

Good enough to demo and test the whole flow with typed orders like "10 bag atta, 5 box soap",
"atta 10 bags", "pathu soap", "make atta 20", "remove item 2". It can't read photos or voice notes.
"""

import re

from app.services.ai.schema import Change, ExtractedLine, Extraction

NUMBER_WORDS = {
    # English
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12, "fifteen": 15, "twenty": 20, "twentyfive": 25, "thirty": 30, "forty": 40,
    "fifty": 50, "hundred": 100, "dozen": 12, "half": 0.5,
    # Tamil (Tanglish)
    "onnu": 1, "oru": 1, "rendu": 2, "moonu": 3, "naalu": 4, "anju": 5, "aaru": 6, "ezhu": 7, "ettu": 8,
    "onbadhu": 9, "pathu": 10, "padhu": 10, "irupathu": 20, "muppathu": 30, "aimbathu": 50, "nooru": 100,
    # Hindi (Hinglish)
    "ek": 1, "do": 2, "teen": 3, "char": 4, "chaar": 4, "paanch": 5, "panch": 5, "chhe": 6, "saat": 7, "aath": 8,
    "nau": 9, "das": 10, "bees": 20, "tees": 30, "pachas": 50, "sau": 100,
}
UNITS = {
    "bag", "bags", "box", "boxes", "kg", "kgs", "kilo", "kilos", "g", "gm", "gms", "pcs", "pc", "piece", "pieces",
    "nos", "no", "packet", "packets", "pkt", "pkts", "pack", "packs", "carton", "cartons", "ctn", "case", "cases",
    "bottle", "bottles", "ltr", "litre", "liter", "litres", "l", "tin", "tins", "bundle", "bundles", "unit", "units",
    "sack", "sacks", "mootai", "moota", "dabba", "x",
}
FILLER = {"of", "please", "pls", "plz", "send", "need", "want", "venum", "vendum", "chahiye", "bhejo", "anuppunga",
          "and", "also", "more", "kodunga", "dena", "i", "we", "me", "us", "the", "a", "an", "kindly", "order"}

STATUS_RX = re.compile(
    r"(where\s+is\s+my\s+order|order\s+status|status\s+of\s+(my\s+)?order|track\s+(my\s+)?order|my\s+orders?\b"
    r"|^\s*status\s*\??\s*$|order\s+(enga|enge|eppo|kahan|kaha|kab)|mera\s+order|delivery\s+(eppo|kab|when))",
    re.IGNORECASE,
)
PRICE_RX = re.compile(r"\b(price|rate|rates|cost|evlo|evvalavu|kitna|kitne|how\s+much|mrp)\b", re.IGNORECASE)
LEDGER_RX = re.compile(r"\b(ledger|statement|outstanding|balance|account\s+copy|dues?)\b", re.IGNORECASE)
PAYMENT_RX = re.compile(r"\b(paid|payment|utr|neft|imps|upi|transferred|gpay|phonepe|cheque)\b", re.IGNORECASE)
COMPLAINT_RX = re.compile(r"\b(damaged?|broken|wrong|missing|leak(ing|ed)?|complaint|expired|short)\b", re.IGNORECASE)
GREETING_RX = re.compile(r"^\s*(hi+|hello|hey|vanakkam|namaste|namaskar|good\s+(morning|evening|afternoon))\W*\s*$",
                         re.IGNORECASE)
REMOVE_RX = re.compile(r"^\s*(remove|delete|cancel|drop|venam|vendam|hatao)\s+(item\s*|no\.?\s*)?#?(\d+)\s*$", re.I)
REMOVE_NAME_RX = re.compile(r"^\s*(remove|delete|drop|hatao)\s+(.+?)\s*$", re.I)
SET_RX = re.compile(r"^\s*(make|change|set|update)\s+(item\s*#?(\d+)|(.+?))\s+(to\s+|as\s+|=\s*)?([\d.]+|\w+)\s*\w*\s*$",
                    re.I)
ADD_RX = re.compile(r"^\s*(add|also|plus)\s+(.+)$", re.I)
NUM_RX = re.compile(r"(?<![\w.])(\d+(?:\.\d+)?)\s*(?:x|nos?|pcs)?(?![\w.])", re.I)


def _num(tok: str) -> float | None:
    t = tok.lower().strip()
    t = re.sub(r"^(\d+(?:\.\d+)?)(x|pcs|nos|kg|kgs|bags?|box(es)?)$", r"\1", t)
    try:
        return float(t)
    except ValueError:
        return NUMBER_WORDS.get(t)


def _segments(text: str) -> list[str]:
    parts = re.split(r"[\n,;]+|\s+&\s+|\s+\+\s+|\s+and\s+", text)
    out = []
    for p in parts:
        p = re.sub(r"^\s*(\d+[.)]|[-*•])\s+", "", p).strip()  # list numbering / bullets
        if p:
            out.append(p)
    return out


def parse_line(seg: str) -> ExtractedLine | None:
    """'10 bag atta' / 'atta - 10 bags' / 'pathu soap' -> text, quantity, unit."""
    tokens = re.findall(r"[\w.]+", seg.lower())
    qty: float | None = None
    unit: str | None = None
    words: list[str] = []
    for tok in tokens:
        n = _num(tok)
        if n is not None and qty is None and not re.fullmatch(r"\d+(\.\d+)?(g|gm|kg|ml|l|ltr)", tok):
            qty = n
            continue
        if tok in UNITS and unit is None:
            unit = tok
            continue
        if tok in FILLER:
            continue
        words.append(tok)
    if qty is None:  # "10kg sugar": the size is the only number, so it is the quantity
        for i, w in enumerate(words):
            m = re.fullmatch(r"(\d+(?:\.\d+)?)(kg|g|gm|l|ltr|ml)", w)
            if m:
                qty, unit = float(m.group(1)), m.group(2)
                words.pop(i)
                break
    text = " ".join(words).strip()
    if not text:
        return None
    return ExtractedLine(text=text, quantity=qty, unit=unit)


def parse(text: str | None, has_open_quote: bool = False) -> Extraction:
    t = (text or "").strip()
    if not t:
        return Extraction(intent="other")
    if GREETING_RX.match(t):
        return Extraction(intent="greeting", language="en")
    if STATUS_RX.search(t):
        return Extraction(intent="order_status")

    if has_open_quote:
        changes: list[Change] = []
        for seg in _segments(t):
            if m := REMOVE_RX.match(seg):
                changes.append(Change(action="remove", item_number=int(m.group(3))))
            elif m := SET_RX.match(seg):
                q = _num(m.group(6))
                if q is not None:
                    changes.append(Change(action="set_quantity", item_number=int(m.group(3)) if m.group(3) else None,
                                          text=(m.group(4) or None), quantity=q))
            elif m := ADD_RX.match(seg):
                line = parse_line(m.group(2))
                if line:
                    changes.append(Change(action="add", text=line.text, quantity=line.quantity or 1))
            elif m := REMOVE_NAME_RX.match(seg):
                changes.append(Change(action="remove", text=m.group(2)))
        if changes:
            return Extraction(intent="order_change", changes=changes)

    lines = [l for l in (parse_line(s) for s in _segments(PRICE_RX.sub(" ", t) if PRICE_RX.search(t) else t)) if l]
    if PRICE_RX.search(t):
        return Extraction(intent="price_enquiry", lines=lines)
    if LEDGER_RX.search(t):
        return Extraction(intent="ledger")
    if PAYMENT_RX.search(t):
        return Extraction(intent="payment")
    if COMPLAINT_RX.search(t):
        return Extraction(intent="complaint")
    with_qty = [l for l in lines if l.quantity is not None]
    if with_qty:
        return Extraction(intent="order", lines=lines)
    return Extraction(intent="other")
