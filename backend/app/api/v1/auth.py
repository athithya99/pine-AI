import uuid
from datetime import UTC, datetime, timedelta

import jwt
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core import security
from app.core.config import settings
from app.core.database import get_db
from app.core.deps import get_current_user
from app.models import OtpCode, Tenant, TenantStatus, User
from app.schemas.accounts import (
    MeOut,
    OtpRequest,
    OtpVerify,
    PasswordLogin,
    PasswordResetConfirm,
    PasswordResetRequest,
    RefreshRequest,
    TenantOut,
    TokenPair,
)
from app.services import notify

router = APIRouter(prefix="/auth", tags=["Auth"])

INVALID_LOGIN = "Invalid login details"


def _issue_tokens(db: Session, user: User) -> TokenPair:
    if user.tenant_id:
        tenant = db.get(Tenant, user.tenant_id)
        if tenant is None or tenant.status != TenantStatus.active:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Company account is suspended")
    user.last_login_at = datetime.now(UTC)
    db.commit()
    return TokenPair(
        access_token=security.create_access_token(user.id, user.token_version),
        refresh_token=security.create_refresh_token(user.id, user.token_version),
    )


@router.post("/login", response_model=TokenPair)
def login(body: PasswordLogin, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(func.lower(User.email) == body.email.lower()))
    if user is None or not user.is_active or not security.verify_password(body.password, user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, INVALID_LOGIN)
    return _issue_tokens(db, user)


@router.post("/otp/request", status_code=status.HTTP_202_ACCEPTED)
def request_otp(body: OtpRequest, db: Session = Depends(get_db)):
    window_start = datetime.now(UTC) - timedelta(minutes=settings.OTP_WINDOW_MINUTES)
    recent = db.scalar(
        select(func.count()).select_from(OtpCode).where(
            OtpCode.mobile == body.mobile, OtpCode.created_at >= window_start
        )
    )
    if recent >= settings.OTP_MAX_REQUESTS:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            f"Too many OTP requests. Try again after {settings.OTP_WINDOW_MINUTES} minutes.",
        )
    user = db.scalar(select(User).where(User.mobile == body.mobile, User.is_active.is_(True)))
    # Same response whether or not the number exists, so numbers can't be probed.
    if user is not None:
        code = security.generate_otp()
        db.add(
            OtpCode(
                mobile=body.mobile,
                code_hash=security.hash_otp(body.mobile, code),
                expires_at=datetime.now(UTC) + timedelta(minutes=settings.OTP_TTL_MINUTES),
            )
        )
        db.commit()
        notify.send_otp(body.mobile, code)
    else:
        # still count the request towards the limit
        db.add(OtpCode(mobile=body.mobile, code_hash="-", expires_at=datetime.now(UTC), used=True))
        db.commit()
    return {"detail": "If this number is registered, an OTP has been sent."}


@router.post("/otp/verify", response_model=TokenPair)
def verify_otp(body: OtpVerify, db: Session = Depends(get_db)):
    otp = db.scalar(
        select(OtpCode)
        .where(OtpCode.mobile == body.mobile, OtpCode.used.is_(False), OtpCode.expires_at > datetime.now(UTC))
        .order_by(OtpCode.created_at.desc())
        .limit(1)
    )
    if otp is None or otp.attempts >= 5:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "OTP expired or invalid. Request a new one.")
    if otp.code_hash != security.hash_otp(body.mobile, body.code):
        otp.attempts += 1
        db.commit()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Incorrect OTP")
    otp.used = True
    user = db.scalar(select(User).where(User.mobile == body.mobile))
    if user is None or not user.is_active:
        db.commit()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, INVALID_LOGIN)
    return _issue_tokens(db, user)


@router.post("/refresh", response_model=TokenPair)
def refresh(body: RefreshRequest, db: Session = Depends(get_db)):
    try:
        payload = security.decode_token(body.refresh_token, "refresh")
        user = db.get(User, uuid.UUID(payload["sub"]))
    except (jwt.PyJWTError, KeyError, ValueError):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Session expired. Please log in again.") from None
    if user is None or not user.is_active or user.token_version != payload.get("ver"):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Session expired. Please log in again.")
    return _issue_tokens(db, user)


@router.post("/logout-all", status_code=status.HTTP_204_NO_CONTENT)
def logout_everywhere(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    user.token_version += 1
    db.commit()


@router.get("/me", response_model=MeOut)
def me(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    out = MeOut.model_validate(user)
    if user.tenant_id:
        out.tenant = TenantOut.model_validate(db.get(Tenant, user.tenant_id))
    return out


@router.post("/password-reset/request", status_code=status.HTTP_202_ACCEPTED)
def password_reset_request(body: PasswordResetRequest, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(func.lower(User.email) == body.email.lower(), User.is_active.is_(True)))
    if user is not None:
        token = jwt.encode(
            {
                "sub": str(user.id),
                "ver": user.token_version,
                "type": "reset",
                "exp": datetime.now(UTC) + timedelta(minutes=30),
            },
            settings.SECRET_KEY,
            algorithm=security.ALGORITHM,
        )
        notify.send_password_reset(user.email, token)
    return {"detail": "If this email is registered, a reset link has been sent."}


@router.post("/password-reset/confirm", status_code=status.HTTP_204_NO_CONTENT)
def password_reset_confirm(body: PasswordResetConfirm, db: Session = Depends(get_db)):
    try:
        payload = security.decode_token(body.token, "reset")
        user = db.get(User, uuid.UUID(payload["sub"]))
    except (jwt.PyJWTError, KeyError, ValueError):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Reset link is invalid or expired") from None
    # token_version check makes each link single-use
    if user is None or not user.is_active or user.token_version != payload.get("ver"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Reset link is invalid or expired")
    user.password_hash = security.hash_password(body.new_password)
    user.token_version += 1
    db.commit()
