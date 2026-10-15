import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.api.v1.tenants import _email_or_mobile_taken
from app.core.database import get_db
from app.core.deps import MANAGERS, require_roles
from app.core.security import hash_password
from app.models import AuditLog, Customer, Role, User
from app.schemas.accounts import AuditLogOut, UserCreate, UserOut, UserUpdate
from app.services import audit

router = APIRouter(tags=["Users"])
managers = require_roles(*MANAGERS)


def _get_user(db: Session, tenant_id: uuid.UUID, user_id: uuid.UUID) -> User:
    user = db.scalar(select(User).where(User.id == user_id, User.tenant_id == tenant_id))
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    return user


@router.get("/users", response_model=list[UserOut])
def list_users(
    role: Role | None = None,
    active: bool | None = None,
    q: str | None = None,
    db: Session = Depends(get_db),
    actor: User = Depends(managers),
):
    stmt = select(User).where(User.tenant_id == actor.tenant_id).order_by(User.name)
    if role:
        stmt = stmt.where(User.role == role)
    if active is not None:
        stmt = stmt.where(User.is_active.is_(active))
    if q:
        stmt = stmt.where(User.name.ilike(f"%{q}%") | User.email.ilike(f"%{q}%") | User.mobile.ilike(f"%{q}%"))
    return db.scalars(stmt).all()


@router.post("/users", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def create_user(body: UserCreate, db: Session = Depends(get_db), actor: User = Depends(managers)):
    if body.role == Role.owner and actor.role != Role.owner:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the owner can add another owner")
    if _email_or_mobile_taken(db, body.email, body.mobile):
        raise HTTPException(status.HTTP_409_CONFLICT, "Email or mobile is already used by another user")
    user = User(
        tenant_id=actor.tenant_id,
        name=body.name,
        email=body.email,
        mobile=body.mobile,
        password_hash=hash_password(body.password) if body.password else None,
        role=body.role,
    )
    db.add(user)
    db.flush()
    audit.record(db, actor, "user", user.id, "create", new=audit.snapshot(user))
    db.commit()
    return user


@router.get("/users/{user_id}", response_model=UserOut)
def get_user(user_id: uuid.UUID, db: Session = Depends(get_db), actor: User = Depends(managers)):
    return _get_user(db, actor.tenant_id, user_id)


@router.patch("/users/{user_id}", response_model=UserOut)
def update_user(user_id: uuid.UUID, body: UserUpdate, db: Session = Depends(get_db), actor: User = Depends(managers)):
    user = _get_user(db, actor.tenant_id, user_id)
    data = body.model_dump(exclude_unset=True)

    if actor.role != Role.owner and (user.role == Role.owner or data.get("role") == Role.owner):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the owner can change an owner account")
    if user.id == actor.id and (data.get("is_active") is False or ("role" in data and data["role"] != actor.role)):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "You cannot deactivate yourself or change your own role")
    if user.role == Role.owner and (data.get("is_active") is False or data.get("role", Role.owner) != Role.owner):
        owners = db.scalars(
            select(User).where(User.tenant_id == actor.tenant_id, User.role == Role.owner, User.is_active.is_(True))
        ).all()
        if len(owners) <= 1:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "The company must keep at least one active owner")
    if _email_or_mobile_taken(db, data.get("email"), data.get("mobile"), exclude=user.id):
        raise HTTPException(status.HTTP_409_CONFLICT, "Email or mobile is already used by another user")

    old = audit.snapshot(user)
    for k, v in data.items():
        setattr(user, k, v)
    # deactivation or role change ends existing sessions
    if data.get("is_active") is False or ("role" in data and old["role"] != user.role.value):
        user.token_version += 1
    audit.record(db, actor, "user", user.id, "update", old, audit.snapshot(user))
    db.commit()
    return user


class AssignCustomers(BaseModel):
    customer_ids: list[uuid.UUID] = Field(min_length=1)


@router.post("/users/{user_id}/customers", status_code=status.HTTP_200_OK)
def assign_customers(
    user_id: uuid.UUID, body: AssignCustomers, db: Session = Depends(get_db), actor: User = Depends(managers)
):
    """Assign customers to a sales user (FR-M1-05)."""
    user = _get_user(db, actor.tenant_id, user_id)
    if user.role != Role.sales:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Customers can only be assigned to Sales users")
    result = db.execute(
        update(Customer)
        .where(Customer.tenant_id == actor.tenant_id, Customer.id.in_(body.customer_ids))
        .values(assigned_user_id=user.id)
    )
    audit.record(
        db, actor, "user", user.id, "update", new={"assigned_customers": [str(c) for c in body.customer_ids]}
    )
    db.commit()
    return {"assigned": result.rowcount}


@router.get("/audit-logs", response_model=list[AuditLogOut])
def list_audit_logs(
    entity: str | None = None,
    entity_id: str | None = None,
    limit: int = Query(default=100, le=500),
    db: Session = Depends(get_db),
    actor: User = Depends(managers),
):
    stmt = select(AuditLog).where(AuditLog.tenant_id == actor.tenant_id).order_by(AuditLog.created_at.desc())
    if entity:
        stmt = stmt.where(AuditLog.entity == entity)
    if entity_id:
        stmt = stmt.where(AuditLog.entity_id == entity_id)
    return db.scalars(stmt.limit(limit)).all()
