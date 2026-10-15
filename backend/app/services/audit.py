import enum
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import inspect
from sqlalchemy.orm import Session

from app.models import AuditLog, User

SKIP_FIELDS = {"password_hash", "created_at", "updated_at"}


def _jsonable(value: Any) -> Any:
    if isinstance(value, (uuid.UUID, Decimal)):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, enum.Enum):
        return value.value
    if isinstance(value, list):
        return [_jsonable(v) for v in value]
    return value


def snapshot(obj: Any) -> dict:
    """Column values of an ORM object as a JSON-safe dict."""
    return {
        c.key: _jsonable(getattr(obj, c.key))
        for c in inspect(obj).mapper.column_attrs
        if c.key not in SKIP_FIELDS
    }


def record(
    db: Session,
    user: User | None,
    entity: str,
    entity_id: Any,
    action: str,
    old: dict | None = None,
    new: dict | None = None,
    tenant_id: uuid.UUID | None = None,
) -> None:
    """Write an audit entry (FR-M1-06). For updates, only changed fields are stored."""
    if action == "update" and old is not None and new is not None:
        changed = {k for k in new if old.get(k) != new.get(k)}
        if not changed:
            return
        old = {k: old.get(k) for k in changed}
        new = {k: new[k] for k in changed}
    db.add(
        AuditLog(
            tenant_id=user.tenant_id if user else tenant_id,
            user_id=user.id if user else None,
            entity=entity,
            entity_id=str(entity_id),
            action=action,
            old_values=old,
            new_values=new,
        )
    )
