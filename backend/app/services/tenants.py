from decimal import Decimal

from sqlalchemy.orm import Session

from app.models import GstRate, Tenant

# Slabs after the 22 Sept 2025 GST rationalisation. Tenants can add or disable rates.
DEFAULT_GST_RATES = [
    (Decimal("0"), "Nil / Exempt"),
    (Decimal("5"), "GST 5%"),
    (Decimal("18"), "GST 18%"),
    (Decimal("40"), "GST 40%"),
]


def seed_tenant_defaults(db: Session, tenant: Tenant) -> None:
    for rate, label in DEFAULT_GST_RATES:
        db.add(GstRate(tenant_id=tenant.id, rate=rate, label=label))
