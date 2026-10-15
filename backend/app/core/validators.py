import re

import phonenumbers

GSTIN_RE = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$")
PINCODE_RE = re.compile(r"^[1-9][0-9]{5}$")
HSN_RE = re.compile(r"^[0-9]{4,8}$")


def normalize_phone(value: str, default_region: str = "IN") -> str:
    """Return the number in E.164 (e.g. +919876543210) or raise ValueError."""
    raw = str(value).strip()
    if raw.endswith(".0"):  # numbers read from Excel as floats
        raw = raw[:-2]
    try:
        parsed = phonenumbers.parse(raw, default_region)
    except phonenumbers.NumberParseException as exc:
        raise ValueError("Invalid phone number") from exc
    if not phonenumbers.is_valid_number(parsed):
        raise ValueError("Invalid phone number")
    return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)


def validate_gstin(value: str) -> str:
    gstin = value.strip().upper()
    if not GSTIN_RE.match(gstin):
        raise ValueError("Invalid GSTIN format")
    return gstin


def validate_pincode(value: str) -> str:
    pin = str(value).strip()
    if pin.endswith(".0"):
        pin = pin[:-2]
    if not PINCODE_RE.match(pin):
        raise ValueError("Pincode must be 6 digits")
    return pin


def validate_hsn(value: str) -> str:
    hsn = str(value).strip()
    if hsn.endswith(".0"):
        hsn = hsn[:-2]
    if not HSN_RE.match(hsn):
        raise ValueError("HSN code must be 4 to 8 digits")
    return hsn
