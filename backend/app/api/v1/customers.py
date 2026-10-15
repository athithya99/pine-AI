import uuid
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Depends, File, HTTPException, Query, Response, UploadFile, status
from pydantic import ValidationError
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import ALL_STAFF, MANAGERS, require_roles
from app.models import Customer, CustomerStatus, CustomerType, PriceList, Role, User
from app.schemas.common import ImportResult, ImportRowError, Page
from app.schemas.customers import CustomerCreate, CustomerOut, CustomerUpdate
from app.services import audit, excel

router = APIRouter(prefix="/customers", tags=["Customers"])
staff = require_roles(*ALL_STAFF)
managers = require_roles(*MANAGERS)

# Commercial terms only Owner/Admin may change (Sales can edit contact details of own customers).
MANAGER_ONLY_FIELDS = {"price_list_id", "assigned_user_id", "credit_limit", "credit_days", "opening_balance", "status"}


def scoped(stmt, user: User):
    """Tenant filter + Sales sees only assigned customers (FR-M1-05)."""
    stmt = stmt.where(Customer.tenant_id == user.tenant_id)
    if user.role == Role.sales:
        stmt = stmt.where(Customer.assigned_user_id == user.id)
    return stmt


def get_customer_or_404(db: Session, user: User, customer_id: uuid.UUID) -> Customer:
    customer = db.scalar(scoped(select(Customer).where(Customer.id == customer_id), user))
    if customer is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Customer not found")
    return customer


def _check_refs(db: Session, tenant_id: uuid.UUID, price_list_id, assigned_user_id) -> None:
    if price_list_id and not db.scalar(
        select(PriceList.id).where(PriceList.id == price_list_id, PriceList.tenant_id == tenant_id)
    ):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Price list not found")
    if assigned_user_id:
        u = db.scalar(select(User).where(User.id == assigned_user_id, User.tenant_id == tenant_id))
        if u is None or u.role != Role.sales:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Assigned user must be an active Sales user")


def _number_taken(db: Session, tenant_id: uuid.UUID, number: str, exclude: uuid.UUID | None = None) -> bool:
    q = select(Customer.id).where(Customer.tenant_id == tenant_id, Customer.whatsapp_number == number)
    if exclude:
        q = q.where(Customer.id != exclude)
    return db.scalar(q) is not None


def _apply_opt_in(customer: Customer, opt_in: bool | None, source: str | None) -> None:
    if opt_in is None:
        return
    if opt_in and not customer.whatsapp_opt_in:
        customer.opt_in_at = datetime.now(UTC)
        customer.opt_in_source = source or "manual"
        customer.opted_out = False
    if not opt_in:
        customer.opt_in_at = None
    customer.whatsapp_opt_in = opt_in


@router.get("", response_model=Page[CustomerOut])
def list_customers(
    q: str | None = None,
    customer_type: CustomerType | None = None,
    status_filter: CustomerStatus | None = Query(default=None, alias="status"),
    tag: list[str] | None = Query(default=None),
    city: str | None = None,
    state: str | None = None,
    assigned_user_id: uuid.UUID | None = None,
    opt_in: bool | None = None,
    page: int = Query(default=1, ge=1),
    size: int = Query(default=25, ge=1, le=200),
    db: Session = Depends(get_db),
    user: User = Depends(staff),
):
    stmt = scoped(select(Customer), user)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(
            or_(
                Customer.business_name.ilike(like),
                Customer.contact_person.ilike(like),
                Customer.whatsapp_number.ilike(like),
                Customer.gstin.ilike(like),
            )
        )
    if customer_type:
        stmt = stmt.where(Customer.customer_type == customer_type)
    if status_filter:
        stmt = stmt.where(Customer.status == status_filter)
    if tag:
        stmt = stmt.where(Customer.tags.overlap([t.lower() for t in tag]))
    if city:
        stmt = stmt.where(Customer.city.ilike(city))
    if state:
        stmt = stmt.where(Customer.state.ilike(state))
    if assigned_user_id:
        stmt = stmt.where(Customer.assigned_user_id == assigned_user_id)
    if opt_in is not None:
        stmt = stmt.where(Customer.whatsapp_opt_in.is_(opt_in))

    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    items = db.scalars(stmt.order_by(Customer.business_name).offset((page - 1) * size).limit(size)).all()
    return Page(items=items, total=total, page=page, size=size)


@router.post("", response_model=CustomerOut, status_code=status.HTTP_201_CREATED)
def create_customer(body: CustomerCreate, db: Session = Depends(get_db), user: User = Depends(managers)):
    if _number_taken(db, user.tenant_id, body.whatsapp_number):
        raise HTTPException(status.HTTP_409_CONFLICT, "Another customer already uses this WhatsApp number")
    _check_refs(db, user.tenant_id, body.price_list_id, body.assigned_user_id)
    data = body.model_dump(exclude={"whatsapp_opt_in", "opt_in_source"})
    customer = Customer(tenant_id=user.tenant_id, **data)
    _apply_opt_in(customer, body.whatsapp_opt_in, body.opt_in_source)
    db.add(customer)
    db.flush()
    audit.record(db, user, "customer", customer.id, "create", new=audit.snapshot(customer))
    db.commit()
    return customer


# --- Excel import / export (declared before /{customer_id} so paths don't clash) ---
IMPORT_COLUMNS = [
    ("business_name", True, "Shop or company name"),
    ("whatsapp_number", True, "10-digit Indian mobile or full number with country code, e.g. 9876543210"),
    ("contact_person", False, "Person to talk to"),
    ("alternate_phone", False, "Another phone number"),
    ("email", False, "Email address"),
    ("gstin", False, "15-character GSTIN, e.g. 33AABCP1234K1Z5"),
    ("billing_address", False, "Full billing address"),
    ("shipping_address", False, "Full shipping address"),
    ("city", False, "City"),
    ("state", False, "State, e.g. Tamil Nadu"),
    ("pincode", False, "6-digit pincode"),
    ("customer_type", False, "distributor, retailer, dealer or other (default retailer)"),
    ("tags", False, "Comma separated, e.g. coimbatore, wholesale"),
    ("price_list", False, "Exact name of an existing price list"),
    ("sales_user_email", False, "Email of the Sales user who handles this customer"),
    ("credit_limit", False, "Amount in Rs, e.g. 50000"),
    ("credit_days", False, "Days allowed to pay, e.g. 30"),
    ("opening_balance", False, "Amount the customer owes you today in Rs, e.g. 12000"),
    ("whatsapp_opt_in", False, "yes if the customer agreed to receive WhatsApp messages, else no"),
]


@router.get("/import/template")
def customer_import_template(_: User = Depends(managers)):
    content = excel.build_template(
        IMPORT_COLUMNS,
        ["Sri Murugan Stores", "9876543210", "Murugan", None, None, None, "12 Main Rd", None,
         "Coimbatore", "Tamil Nadu", "641001", "retailer", "coimbatore, wholesale", None, None, 50000, 30, 0, "yes"],
    )
    return Response(
        content, media_type=excel.XLSX_MIME,
        headers={"Content-Disposition": 'attachment; filename="customer_import_template.xlsx"'},
    )


def _decimal(v, field: str) -> Decimal | None:
    if v is None or str(v).strip() == "":
        return None
    try:
        return Decimal(str(v).replace(",", ""))
    except InvalidOperation:
        raise ValueError(f"{field}: must be a number") from None


@router.post("/import", response_model=ImportResult)
async def import_customers(file: UploadFile = File(...), db: Session = Depends(get_db), user: User = Depends(managers)):
    """Imports valid rows, skips invalid ones and reports why (FR-M2-04)."""
    try:
        rows = excel.read_rows(await file.read(), ["business_name", "whatsapp_number"])
    except excel.ImportFileError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from None

    tid = user.tenant_id
    price_lists = {p.name.lower(): p.id for p in db.scalars(select(PriceList).where(PriceList.tenant_id == tid))}
    sales_users = {
        u.email.lower(): u.id
        for u in db.scalars(select(User).where(User.tenant_id == tid, User.role == Role.sales, User.email.is_not(None)))
    }
    existing = set(db.scalars(select(Customer.whatsapp_number).where(Customer.tenant_id == tid)))
    seen_in_file: dict[str, int] = {}

    errors: list[ImportRowError] = []
    created = 0
    for row_no, r in rows:
        row_errors: list[str] = []
        try:
            payload = {
                "business_name": excel.as_text(r.get("business_name")),
                "whatsapp_number": excel.as_text(r.get("whatsapp_number")),
                "contact_person": excel.as_text(r.get("contact_person")),
                "alternate_phone": excel.as_text(r.get("alternate_phone")),
                "email": excel.as_text(r.get("email")),
                "gstin": excel.as_text(r.get("gstin")),
                "billing_address": excel.as_text(r.get("billing_address")),
                "shipping_address": excel.as_text(r.get("shipping_address")),
                "city": excel.as_text(r.get("city")),
                "state": excel.as_text(r.get("state")),
                "pincode": excel.as_text(r.get("pincode")),
                "tags": excel.as_list(r.get("tags")),
                "whatsapp_opt_in": excel.as_bool(r.get("whatsapp_opt_in")),
                "opt_in_source": "excel_import",
            }
            ctype = excel.as_text(r.get("customer_type"))
            if ctype:
                payload["customer_type"] = ctype.lower()
            for f in ("credit_limit", "credit_days", "opening_balance"):
                val = _decimal(r.get(f), f)
                if val is not None:
                    payload[f] = int(val) if f == "credit_days" else val
            pl = excel.as_text(r.get("price_list"))
            if pl:
                if pl.lower() not in price_lists:
                    row_errors.append(f"price_list: '{pl}' does not exist")
                else:
                    payload["price_list_id"] = price_lists[pl.lower()]
            su = excel.as_text(r.get("sales_user_email"))
            if su:
                if su.lower() not in sales_users:
                    row_errors.append(f"sales_user_email: no Sales user with email '{su}'")
                else:
                    payload["assigned_user_id"] = sales_users[su.lower()]
            data = CustomerCreate(**payload)
        except ValidationError as exc:
            row_errors.extend(excel.pydantic_errors(exc))
            data = None
        except ValueError as exc:
            row_errors.append(str(exc))
            data = None

        if data is not None:
            num = data.whatsapp_number
            if num in existing:
                row_errors.append("whatsapp_number: a customer with this number already exists")
            elif num in seen_in_file:
                row_errors.append(f"whatsapp_number: duplicate of row {seen_in_file[num]} in this file")

        if row_errors:
            errors.append(ImportRowError(row=row_no, errors=row_errors))
            continue

        customer = Customer(tenant_id=tid, **data.model_dump(exclude={"whatsapp_opt_in", "opt_in_source"}))
        _apply_opt_in(customer, data.whatsapp_opt_in, data.opt_in_source)
        db.add(customer)
        seen_in_file[data.whatsapp_number] = row_no
        created += 1

    audit.record(db, user, "customer_import", file.filename or "upload", "create",
                 new={"created": created, "failed": len(errors)})
    db.commit()
    return ImportResult(total_rows=len(rows), created=created, updated=0, failed=len(errors), errors=errors)


@router.get("/export")
def export_customers(db: Session = Depends(get_db), user: User = Depends(staff)):
    customers = db.scalars(scoped(select(Customer), user).order_by(Customer.business_name)).all()
    price_lists = {p.id: p.name for p in db.scalars(select(PriceList).where(PriceList.tenant_id == user.tenant_id))}
    users = {u.id: u.email or u.name for u in db.scalars(select(User).where(User.tenant_id == user.tenant_id))}
    headers = [c for c, _, _ in IMPORT_COLUMNS] + ["status"]
    rows = [
        [
            c.business_name, c.whatsapp_number, c.contact_person, c.alternate_phone, c.email, c.gstin,
            c.billing_address, c.shipping_address, c.city, c.state, c.pincode, c.customer_type.value,
            ", ".join(c.tags or []), price_lists.get(c.price_list_id), users.get(c.assigned_user_id),
            float(c.credit_limit), c.credit_days, float(c.opening_balance), "yes" if c.whatsapp_opt_in else "no", c.status.value,
        ]
        for c in customers
    ]
    return Response(
        excel.export_rows(headers, rows, "Customers"), media_type=excel.XLSX_MIME,
        headers={"Content-Disposition": 'attachment; filename="customers.xlsx"'},
    )


@router.get("/{customer_id}", response_model=CustomerOut)
def get_customer(customer_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(staff)):
    return get_customer_or_404(db, user, customer_id)


@router.patch("/{customer_id}", response_model=CustomerOut)
def update_customer(
    customer_id: uuid.UUID, body: CustomerUpdate, db: Session = Depends(get_db),
    user: User = Depends(require_roles(Role.owner, Role.admin, Role.sales)),
):
    customer = get_customer_or_404(db, user, customer_id)
    data = body.model_dump(exclude_unset=True)
    if user.role == Role.sales and MANAGER_ONLY_FIELDS & data.keys():
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Sales users cannot change price list, credit terms, opening balance, assignment or status"
        )
    if data.get("whatsapp_number") and _number_taken(db, user.tenant_id, data["whatsapp_number"], customer.id):
        raise HTTPException(status.HTTP_409_CONFLICT, "Another customer already uses this WhatsApp number")
    if "whatsapp_number" in data and not data["whatsapp_number"]:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "WhatsApp number is required")
    if "business_name" in data and not data["business_name"]:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Business name is required")
    _check_refs(db, user.tenant_id, data.get("price_list_id"), data.get("assigned_user_id"))

    old = audit.snapshot(customer)
    opt_in = data.pop("whatsapp_opt_in", None)
    source = data.pop("opt_in_source", None)
    for k, v in data.items():
        setattr(customer, k, v)
    _apply_opt_in(customer, opt_in, source)
    audit.record(db, user, "customer", customer.id, "update", old, audit.snapshot(customer))
    db.commit()
    return customer


@router.post("/{customer_id}/deactivate", response_model=CustomerOut)
def deactivate_customer(customer_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(managers)):
    customer = get_customer_or_404(db, user, customer_id)
    old = audit.snapshot(customer)
    customer.status = CustomerStatus.inactive
    audit.record(db, user, "customer", customer.id, "update", old, audit.snapshot(customer))
    db.commit()
    return customer
