import uuid
from collections.abc import Callable

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import decode_token
from app.models import Role, Tenant, TenantStatus, User

bearer = HTTPBearer(auto_error=False)


def get_current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: Session = Depends(get_db),
) -> User:
    unauthorized = HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
    if creds is None:
        raise unauthorized
    try:
        payload = decode_token(creds.credentials, "access")
        user_id = uuid.UUID(payload["sub"])
    except (jwt.PyJWTError, KeyError, ValueError):
        raise unauthorized from None

    user = db.get(User, user_id)
    if user is None or not user.is_active or user.token_version != payload.get("ver"):
        raise unauthorized
    if user.tenant_id is not None:
        tenant = db.get(Tenant, user.tenant_id)
        if tenant is None or tenant.status != TenantStatus.active:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Company account is suspended")
    return user


def require_roles(*roles: Role) -> Callable[[User], User]:
    def checker(user: User = Depends(get_current_user)) -> User:
        if user.role not in roles:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "You do not have permission for this action")
        return user

    return checker


def get_tenant_user(user: User = Depends(get_current_user)) -> User:
    """Any seller-company user. Super admins have no tenant data."""
    if user.tenant_id is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This endpoint is for company users")
    return user


ALL_STAFF = (Role.owner, Role.admin, Role.sales, Role.accounts)
MANAGERS = (Role.owner, Role.admin)
