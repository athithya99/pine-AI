import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import MANAGERS, get_tenant_user, require_roles
from app.core.security import hash_password
from app.models import Role, Tenant, TenantStatus, User
from app.schemas.accounts import TenantCreate, TenantOut, TenantUpdate
from app.services import audit
from app.services.tenants import seed_tenant_defaults

router = APIRouter(tags=["Tenants"])
super_admin = require_roles(Role.super_admin)


def _email_or_mobile_taken(db: Session, email: str | None, mobile: str | None, exclude: uuid.UUID | None = None):
    conds = []
    if email:
        conds.append(func.lower(User.email) == email.lower())
    if mobile:
        conds.append(User.mobile == mobile)
    if not conds:
        return False
    q = select(User.id).where(or_(*conds))
    if exclude:
        q = q.where(User.id != exclude)
    return db.scalar(q.limit(1)) is not None


# ---------- platform (super admin) ----------
@router.post("/admin/tenants", response_model=TenantOut, status_code=status.HTTP_201_CREATED)
def create_tenant(body: TenantCreate, db: Session = Depends(get_db), actor: User = Depends(super_admin)):
    if _email_or_mobile_taken(db, body.owner_email, body.owner_mobile):
        raise HTTPException(status.HTTP_409_CONFLICT, "Owner email or mobile is already used by another user")
    data = body.model_dump(exclude={"owner_name", "owner_email", "owner_mobile", "owner_password"})
    tenant = Tenant(**data)
    db.add(tenant)
    db.flush()
    owner = User(
        tenant_id=tenant.id,
        name=body.owner_name,
        email=body.owner_email,
        mobile=body.owner_mobile,
        password_hash=hash_password(body.owner_password),
        role=Role.owner,
    )
    db.add(owner)
    seed_tenant_defaults(db, tenant)
    db.flush()
    audit.record(db, actor, "tenant", tenant.id, "create", new=audit.snapshot(tenant))
    db.commit()
    return tenant


@router.get("/admin/tenants", response_model=list[TenantOut])
def list_tenants(
    q: str | None = None,
    status_filter: TenantStatus | None = Query(default=None, alias="status"),
    db: Session = Depends(get_db),
    _: User = Depends(super_admin),
):
    stmt = select(Tenant).order_by(Tenant.created_at.desc())
    if q:
        stmt = stmt.where(Tenant.name.ilike(f"%{q}%"))
    if status_filter:
        stmt = stmt.where(Tenant.status == status_filter)
    return db.scalars(stmt).all()


def _set_status(tenant_id: uuid.UUID, new_status: TenantStatus, db: Session, actor: User) -> Tenant:
    tenant = db.get(Tenant, tenant_id)
    if tenant is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Company not found")
    old = audit.snapshot(tenant)
    tenant.status = new_status
    audit.record(db, actor, "tenant", tenant.id, "update", old, audit.snapshot(tenant))
    db.commit()
    return tenant


@router.post("/admin/tenants/{tenant_id}/suspend", response_model=TenantOut)
def suspend_tenant(tenant_id: uuid.UUID, db: Session = Depends(get_db), actor: User = Depends(super_admin)):
    return _set_status(tenant_id, TenantStatus.suspended, db, actor)


@router.post("/admin/tenants/{tenant_id}/reactivate", response_model=TenantOut)
def reactivate_tenant(tenant_id: uuid.UUID, db: Session = Depends(get_db), actor: User = Depends(super_admin)):
    return _set_status(tenant_id, TenantStatus.active, db, actor)


# ---------- own company settings ----------
@router.get("/company", response_model=TenantOut)
def get_company(db: Session = Depends(get_db), user: User = Depends(get_tenant_user)):
    return db.get(Tenant, user.tenant_id)


@router.patch("/company", response_model=TenantOut)
def update_company(body: TenantUpdate, db: Session = Depends(get_db), user: User = Depends(require_roles(*MANAGERS))):
    tenant = db.get(Tenant, user.tenant_id)
    old = audit.snapshot(tenant)
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(tenant, k, v)
    audit.record(db, user, "tenant", tenant.id, "update", old, audit.snapshot(tenant))
    db.commit()
    return tenant
