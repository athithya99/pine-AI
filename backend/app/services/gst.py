"""GST helpers: state from GSTIN, intra- vs inter-state supply, amount in words (Indian numbering)."""

from decimal import ROUND_HALF_UP, Decimal

# GST state codes (first two digits of a GSTIN)
STATE_CODES = {
    "01": "Jammu and Kashmir", "02": "Himachal Pradesh", "03": "Punjab", "04": "Chandigarh", "05": "Uttarakhand",
    "06": "Haryana", "07": "Delhi", "08": "Rajasthan", "09": "Uttar Pradesh", "10": "Bihar", "11": "Sikkim",
    "12": "Arunachal Pradesh", "13": "Nagaland", "14": "Manipur", "15": "Mizoram", "16": "Tripura",
    "17": "Meghalaya", "18": "Assam", "19": "West Bengal", "20": "Jharkhand", "21": "Odisha", "22": "Chhattisgarh",
    "23": "Madhya Pradesh", "24": "Gujarat", "26": "Dadra and Nagar Haveli and Daman and Diu", "27": "Maharashtra",
    "29": "Karnataka", "30": "Goa", "31": "Lakshadweep", "32": "Kerala", "33": "Tamil Nadu", "34": "Puducherry",
    "35": "Andaman and Nicobar Islands", "36": "Telangana", "37": "Andhra Pradesh", "38": "Ladakh",
}
ALIASES = {"tn": "tamil nadu", "tamilnadu": "tamil nadu", "orissa": "odisha", "pondicherry": "puducherry",
           "new delhi": "delhi", "nct of delhi": "delhi", "bangalore": "karnataka", "j&k": "jammu and kashmir"}

TWO = Decimal("0.01")


def money(v) -> Decimal:
    return Decimal(v).quantize(TWO, rounding=ROUND_HALF_UP)


def norm_state(s: str | None) -> str | None:
    if not s:
        return None
    k = " ".join(s.lower().replace("&", " and ").split())
    k = ALIASES.get(k, k)
    return k or None


def state_name(gstin: str | None, state: str | None) -> str | None:
    """Prefer the state coded in the GSTIN; fall back to the address state."""
    if gstin and len(gstin) >= 2 and gstin[:2] in STATE_CODES:
        return STATE_CODES[gstin[:2]]
    return state.strip().title() if state and state.strip() else None


def state_code(name: str | None) -> str | None:
    n = norm_state(name)
    for code, st in STATE_CODES.items():
        if st.lower() == n:
            return code
    return None


def is_inter_state(seller_state: str | None, buyer_state: str | None) -> bool:
    """IGST when supplier and place of supply are in different states. Unknown buyer state = same state."""
    a, b = norm_state(seller_state), norm_state(buyer_state)
    return bool(a and b and a != b)


def split_tax(taxable: Decimal, rate: Decimal, inter_state: bool) -> tuple[Decimal, Decimal, Decimal]:
    """(cgst, sgst, igst). Intra-state: CGST and SGST at half the rate each; inter-state: IGST at the full rate."""
    if inter_state:
        return Decimal("0.00"), Decimal("0.00"), money(taxable * rate / 100)
    half = money(taxable * rate / 200)
    return half, half, Decimal("0.00")


_ONES = ["", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine", "Ten", "Eleven", "Twelve",
         "Thirteen", "Fourteen", "Fifteen", "Sixteen", "Seventeen", "Eighteen", "Nineteen"]
_TENS = ["", "", "Twenty", "Thirty", "Forty", "Fifty", "Sixty", "Seventy", "Eighty", "Ninety"]


def _below_100(n: int) -> str:
    return _ONES[n] if n < 20 else (_TENS[n // 10] + (" " + _ONES[n % 10] if n % 10 else ""))


def _below_1000(n: int) -> str:
    h, r = divmod(n, 100)
    parts = []
    if h:
        parts.append(f"{_ONES[h]} Hundred")
    if r:
        parts.append(_below_100(r))
    return " ".join(parts)


def _int_words(n: int) -> str:
    if n == 0:
        return "Zero"
    parts = []
    crore, n = divmod(n, 10_000_000)
    lakh, n = divmod(n, 100_000)
    thousand, n = divmod(n, 1000)
    if crore:
        parts.append(f"{_int_words(crore)} Crore")
    if lakh:
        parts.append(f"{_below_100(lakh)} Lakh")
    if thousand:
        parts.append(f"{_below_100(thousand)} Thousand")
    if n:
        parts.append(_below_1000(n))
    return " ".join(parts)


def amount_in_words(amount: Decimal) -> str:
    amount = money(amount)
    rupees = int(amount)
    paise = int((amount - rupees) * 100)
    words = f"Rupees {_int_words(rupees)}"
    if paise:
        words += f" and {_below_100(paise)} Paise"
    return words + " Only"
