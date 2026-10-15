import hashlib
import hmac
import secrets
import uuid
from datetime import UTC, datetime, timedelta

import jwt
from passlib.context import CryptContext

from app.core.config import settings

pwd_context = CryptContext(schemes=["argon2"], deprecated="auto")
ALGORITHM = "HS256"


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(password: str, hashed: str | None) -> bool:
    if not hashed:
        return False
    return pwd_context.verify(password, hashed)


def _create_token(user_id: uuid.UUID, token_version: int, kind: str, expires: timedelta) -> str:
    now = datetime.now(UTC)
    payload = {
        "sub": str(user_id),
        "ver": token_version,
        "type": kind,
        "iat": now,
        "exp": now + expires,
        "jti": secrets.token_hex(8),
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=ALGORITHM)


def create_access_token(user_id: uuid.UUID, token_version: int) -> str:
    return _create_token(user_id, token_version, "access", timedelta(minutes=settings.ACCESS_TOKEN_MINUTES))


def create_refresh_token(user_id: uuid.UUID, token_version: int) -> str:
    return _create_token(user_id, token_version, "refresh", timedelta(days=settings.REFRESH_TOKEN_DAYS))


def decode_token(token: str, expected_type: str) -> dict:
    payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[ALGORITHM])
    if payload.get("type") != expected_type:
        raise jwt.InvalidTokenError("wrong token type")
    return payload


def generate_otp() -> str:
    return "".join(secrets.choice("0123456789") for _ in range(settings.OTP_LENGTH))


def hash_otp(mobile: str, code: str) -> str:
    return hmac.new(settings.SECRET_KEY.encode(), f"{mobile}:{code}".encode(), hashlib.sha256).hexdigest()
