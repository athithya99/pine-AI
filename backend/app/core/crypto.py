"""Encrypt secrets at rest (WhatsApp tokens, app secrets, later gateway keys) - NFR-01.

The Fernet key is derived from SECRET_KEY, so rotating SECRET_KEY means re-entering stored secrets.
"""

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings


def _fernet() -> Fernet:
    key = hashlib.sha256(f"pinesphere-secrets:{settings.SECRET_KEY}".encode()).digest()
    return Fernet(base64.urlsafe_b64encode(key))


def encrypt(value: str | None) -> str | None:
    if not value:
        return None
    return _fernet().encrypt(value.encode()).decode()


def decrypt(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return _fernet().decrypt(value.encode()).decode()
    except InvalidToken:
        return None


def mask(value: str | None) -> str | None:
    if not value:
        return None
    return f"{'•' * 6}{value[-4:]}" if len(value) > 4 else "••••"
