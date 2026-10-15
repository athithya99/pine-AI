import uuid
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Depends, File, HTTPException, Query, Response, UploadFile, status
from pydantic import ValidationError
from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session

from app.api.v1.customers import get_customer_or_404
from app.core.database import get_db
from app.core.deps import ALL_STAFF, MANAGERS, require_roles
from app.models import CustomerPrice, GstRate, PriceList, PriceListItem, Product, Scheme, StockStatus, User
from app.schemas.catalog import (
    GstRateCreate,
    GstRateOut,
    PriceEntriesIn,
    PriceEntryOut,
    PriceListCreate,
    PriceListOut,
    PriceListUpdate,
    ProductCreate,
    ProductOut,
    ProductUpdate,
    QuoteIn,
    QuoteOut,
    SchemeCreate,
    SchemeOut,
    SchemeUpdate,
    StockUpdate,
    validate_scheme_values,
)
from app.schemas.common import ImportResult, ImportRowError, Page
from app.services import audit, excel
from app.services.pricing import PricingEngine, today_ist

router = APIRouter(tags=["Catalog"])
staff = require_roles(*ALL_STAFF)
managers = require_roles(*MANAGERS)


def _active_rates(db: Session, tenant_id: uuid.UUID) -> set[Decimal]:
    return {
        r.normalize()
        for r in db.scalars(select(GstRate.rate).where(GstRate.tenant_id == tenant_id, GstRate.is_active.is_(True)))
    }


def _check_gst(db: Session, tenant_id: uuid.UUID, rate: Decimal | None) -> None:
    if rate is not None and Decimal(rate).normalize() not in _active_rates(db, tenant_id):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"GST rate {rate}% is not in your GST rate master")


def _product(db: Session, tenant_id: uuid.UUID, product_id: uuid.UUID) -> Product:
    p = db.scalar(select(Product).where(Product.id == product_id, Product.tenant_id == tenant_id))
    if p is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Product not found")
    return p


def _products_exist(db: Session, tenant_id: uuid.UUID, ids: list[uuid.UUID]) -> None:
    found = set(db.scalars(select(Product.id).where(Product.tenant_id == tenant_id, Product.id.in_(ids))))
    missing = set(ids) - found
    if missing:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Unknown product ids: {', '.join(map(str, missing))}")


# ===================== GST rate master =====================
@router.get("/gst-rates", response_model=list[GstRateOut])
def list_gst_rates(db: Session = Depends(get_db), user: User = Depends(staff)):
    return db.scalars(select(GstRate).where(GstRate.tenant_id == user.tenant_id).order_by(GstRate.rate)).all()


@router.post("/gst-rates", response_model=GstRateOut, status_code=status.HTTP_201_CREATED)
def add_gst_rate(body: GstRateCreate, db: Session = Depends(get_db), user: User = Depends(managers)):
    existing = db.scalar(select(GstRate).where(GstRate.tenant_id == user.tenant_id, GstRate.rate == body.rate))
    if existing:
        existing.is_active = True
        db.commit()
        return existing
    rate = GstRate(tenant_id=user.tenant_id, rate=body.rate, label=body.label or f"GST {body.rate.normalize()}%")
    db.add(rate)
    db.flush()
    audit.record(db, user, "gst_rate", rate.id, "create", new=audit.snapshot(rate))
    db.commit()
    return rate


@router.post("/gst-rates/{rate_id}/deactivate", response_model=GstRateOut)
def deactivate_gst_rate(rate_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(managers)):
    rate = db.scalar(select(GstRate).where(GstRate.id == rate_id, GstRate.tenant_id == user.tenant_id))
    if rate is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "GST rate not found")
    in_use = db.scalar(
        select(func.count()).select_from(Product).where(
            Product.tenant_id == user.tenant_id, Product.gst_rate == rate.rate, Product.is_active.is_(True)
        )
    )
    if in_use:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"{in_use} active products still use this rate")
    rate.is_active = False
    audit.record(db, user, "gst_rate", rate.id, "update", {"is_active": True}, {"is_active": False})
    db.commit()
    return rate


# ===================== Products =====================
@router.get("/products", response_model=Page[ProductOut])
def list_products(
    q: str | None = None,
    category: str | None = None,
    brand: str | None = None,
    stock_status: StockStatus | None = None,
    active: bool | None = True,
    page: int = Query(default=1, ge=1),
    size: int = Query(default=25, ge=1, le=200),
    db: Session = Depends(get_db),
    user: User = Depends(staff),
):
    stmt = select(Product).where(Product.tenant_id == user.tenant_id)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(
            or_(
                Product.name.ilike(like),
                Product.sku.ilike(like),
                Product.brand.ilike(like),
                func.array_to_string(Product.aliases, " ").ilike(like),
            )
        )
    if category:
        stmt = stmt.where(Product.category.ilike(category))
    if brand:
        stmt = stmt.where(Product.brand.ilike(brand))
    if stock_status:
        stmt = stmt.where(Product.stock_status == stock_status)
    if active is not None:
        stmt = stmt.where(Product.is_active.is_(active))
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    items = db.scalars(stmt.order_by(Product.name).offset((page - 1) * size).limit(size)).all()
    return Page(items=items, total=total, page=page, size=size)


@router.get("/products/categories", response_model=list[str])
def list_categories(db: Session = Depends(get_db), user: User = Depends(staff)):
    return db.scalars(
        select(Product.category).where(Product.tenant_id == user.tenant_id, Product.category.is_not(None))
        .distinct().order_by(Product.category)
    ).all()


@router.post("/products", response_model=ProductOut, status_code=status.HTTP_201_CREATED)
def create_product(body: ProductCreate, db: Session = Depends(get_db), user: User = Depends(managers)):
    if db.scalar(select(Product.id).where(Product.tenant_id == user.tenant_id, Product.sku == body.sku)):
        raise HTTPException(status.HTTP_409_CONFLICT, f"SKU {body.sku} already exists")
    _check_gst(db, user.tenant_id, body.gst_rate)
    product = Product(tenant_id=user.tenant_id, **body.model_dump())
    db.add(product)
    db.flush()
    audit.record(db, user, "product", product.id, "create", new=audit.snapshot(product))
    db.commit()
    return product


PRODUCT_COLUMNS = [
    ("sku", True, "Unique product code. An existing SKU updates that product."),
    ("name", True, "Product name as buyers know it"),
    ("base_price", True, "Default selling price in Rs, before GST"),
    ("gst_rate", True, "GST % from your rate master, e.g. 5 or 18"),
    ("brand", False, "Brand"),
    ("category", False, "Category"),
    ("uom", False, "Unit, e.g. PCS, BOX, KG, BAG (default PCS)"),
    ("pack_size", False, "e.g. 1 kg, 12 x 500 ml"),
    ("hsn_code", False, "4 to 8 digit HSN code"),
    ("mrp", False, "MRP in Rs"),
    ("min_order_qty", False, "Minimum order quantity (default 1)"),
    ("aliases", False, "Other names buyers use, comma separated (helps AI ordering)"),
    ("stock_status", False, "in_stock, low or out_of_stock (default in_stock)"),
]


@router.get("/products/import/template")
def product_import_template(_: User = Depends(managers)):
    content = excel.build_template(
        PRODUCT_COLUMNS,
        ["SALT-1KG", "Tata Salt 1kg", 24, 5, "Tata", "Staples", "PCS", "1 kg", "2501", 28, 10, "salt, tata uppu",
         "in_stock"],
    )
    return Response(content, media_type=excel.XLSX_MIME,
                    headers={"Content-Disposition": 'attachment; filename="product_import_template.xlsx"'})


def _num(v, field: str) -> Decimal | None:
    s = excel.as_text(v)
    if s is None:
        return None
    try:
        return Decimal(s.replace(",", ""))
    except InvalidOperation:
        raise ValueError(f"{field}: must be a number") from None


@router.post("/products/import", response_model=ImportResult)
async def import_products(file: UploadFile = File(...), db: Session = Depends(get_db), user: User = Depends(managers)):
    """Create new SKUs and update existing ones; invalid rows are skipped and reported (FR-M3-07)."""
    try:
        rows = excel.read_rows(await file.read(), ["sku", "name", "base_price", "gst_rate"])
    except excel.ImportFileError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from None

    tid = user.tenant_id
    rates = _active_rates(db, tid)
    existing = {p.sku: p for p in db.scalars(select(Product).where(Product.tenant_id == tid))}
    seen: dict[str, int] = {}
    errors: list[ImportRowError] = []
    created = updated = 0

    for row_no, r in rows:
        row_errors: list[str] = []
        data = None
        try:
            payload = {
                "sku": excel.as_text(r.get("sku")),
                "name": excel.as_text(r.get("name")),
                "brand": excel.as_text(r.get("brand")),
                "category": excel.as_text(r.get("category")),
                "pack_size": excel.as_text(r.get("pack_size")),
                "hsn_code": excel.as_text(r.get("hsn_code")),
                "aliases": excel.as_list(r.get("aliases")),
            }
            for f in ("base_price", "gst_rate", "mrp", "min_order_qty"):
                val = _num(r.get(f), f)
                if val is not None:
                    payload[f] = val
            if excel.as_text(r.get("uom")):
                payload["uom"] = excel.as_text(r.get("uom")).upper()
            if excel.as_text(r.get("stock_status")):
                payload["stock_status"] = excel.as_text(r.get("stock_status")).lower()
            data = ProductCreate(**payload)
        except ValidationError as exc:
            row_errors.extend(excel.pydantic_errors(exc))
        except ValueError as exc:
            row_errors.append(str(exc))

        if data is not None:
            if data.gst_rate.normalize() not in rates:
                row_errors.append(f"gst_rate: {data.gst_rate}% is not in your GST rate master")
            if data.sku in seen:
                row_errors.append(f"sku: duplicate of row {seen[data.sku]} in this file")

        if row_errors:
            errors.append(ImportRowError(row=row_no, errors=row_errors))
            continue

        seen[data.sku] = row_no
        values = data.model_dump(exclude={"images"} if data.sku in existing else set())
        if data.sku in existing:
            product = existing[data.sku]
            # only overwrite optional columns the sheet actually filled
            sent = {k for k, v in r.items() if v is not None}
            for k, v in values.items():
                if k in {"sku", "name", "base_price", "gst_rate"} or k in sent:
                    setattr(product, k, v)
            updated += 1
        else:
            product = Product(tenant_id=tid, **values)
            db.add(product)
            existing[data.sku] = product
            created += 1

    audit.record(db, user, "product_import", file.filename or "upload", "create",
                 new={"created": created, "updated": updated, "failed": len(errors)})
    db.commit()
    return ImportResult(total_rows=len(rows), created=created, updated=updated, failed=len(errors), errors=errors)


@router.get("/products/export")
def export_products(db: Session = Depends(get_db), user: User = Depends(staff)):
    products = db.scalars(select(Product).where(Product.tenant_id == user.tenant_id).order_by(Product.name)).all()
    headers = [c for c, _, _ in PRODUCT_COLUMNS] + ["active"]
    rows = [
        [p.sku, p.name, float(p.base_price), float(p.gst_rate), p.brand, p.category, p.uom, p.pack_size,
         p.hsn_code, float(p.mrp) if p.mrp is not None else None, float(p.min_order_qty),
         ", ".join(p.aliases or []), p.stock_status.value, "yes" if p.is_active else "no"]
        for p in products
    ]
    return Response(excel.export_rows(headers, rows, "Products"), media_type=excel.XLSX_MIME,
                    headers={"Content-Disposition": 'attachment; filename="products.xlsx"'})


@router.get("/products/{product_id}", response_model=ProductOut)
def get_product(product_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(staff)):
    return _product(db, user.tenant_id, product_id)


@router.patch("/products/{product_id}", response_model=ProductOut)
def update_product(product_id: uuid.UUID, body: ProductUpdate, db: Session = Depends(get_db),
                   user: User = Depends(managers)):
    product = _product(db, user.tenant_id, product_id)
    data = body.model_dump(exclude_unset=True)
    for required in ("sku", "name", "base_price", "gst_rate", "uom"):
        if required in data and data[required] is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"{required} cannot be empty")
    if "sku" in data and data["sku"] != product.sku and db.scalar(
        select(Product.id).where(Product.tenant_id == user.tenant_id, Product.sku == data["sku"])
    ):
        raise HTTPException(status.HTTP_409_CONFLICT, f"SKU {data['sku']} already exists")
    _check_gst(db, user.tenant_id, data.get("gst_rate"))
    old = audit.snapshot(product)
    for k, v in data.items():
        setattr(product, k, v)
    audit.record(db, user, "product", product.id, "update", old, audit.snapshot(product))
    db.commit()
    return product


@router.put("/products/{product_id}/stock", response_model=ProductOut)
def set_stock(product_id: uuid.UUID, body: StockUpdate, db: Session = Depends(get_db), user: User = Depends(managers)):
    product = _product(db, user.tenant_id, product_id)
    old = audit.snapshot(product)
    product.stock_status = body.stock_status
    audit.record(db, user, "product", product.id, "update", old, audit.snapshot(product))
    db.commit()
    return product


# ===================== Price lists =====================
def _price_list(db: Session, tenant_id: uuid.UUID, pl_id: uuid.UUID) -> PriceList:
    pl = db.scalar(select(PriceList).where(PriceList.id == pl_id, PriceList.tenant_id == tenant_id))
    if pl is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Price list not found")
    return pl


@router.get("/price-lists", response_model=list[PriceListOut])
def list_price_lists(db: Session = Depends(get_db), user: User = Depends(staff)):
    return db.scalars(select(PriceList).where(PriceList.tenant_id == user.tenant_id).order_by(PriceList.name)).all()


@router.post("/price-lists", response_model=PriceListOut, status_code=status.HTTP_201_CREATED)
def create_price_list(body: PriceListCreate, db: Session = Depends(get_db), user: User = Depends(managers)):
    if db.scalar(select(PriceList.id).where(PriceList.tenant_id == user.tenant_id,
                                            func.lower(PriceList.name) == body.name.lower())):
        raise HTTPException(status.HTTP_409_CONFLICT, "A price list with this name already exists")
    pl = PriceList(tenant_id=user.tenant_id, **body.model_dump())
    db.add(pl)
    db.flush()
    audit.record(db, user, "price_list", pl.id, "create", new=audit.snapshot(pl))
    db.commit()
    return pl


@router.patch("/price-lists/{pl_id}", response_model=PriceListOut)
def update_price_list(pl_id: uuid.UUID, body: PriceListUpdate, db: Session = Depends(get_db),
                      user: User = Depends(managers)):
    pl = _price_list(db, user.tenant_id, pl_id)
    data = body.model_dump(exclude_unset=True)
    if data.get("name") and data["name"].lower() != pl.name.lower() and db.scalar(
        select(PriceList.id).where(PriceList.tenant_id == user.tenant_id,
                                   func.lower(PriceList.name) == data["name"].lower())
    ):
        raise HTTPException(status.HTTP_409_CONFLICT, "A price list with this name already exists")
    old = audit.snapshot(pl)
    for k, v in data.items():
        if v is not None or k == "description":
            setattr(pl, k, v)
    audit.record(db, user, "price_list", pl.id, "update", old, audit.snapshot(pl))
    db.commit()
    return pl


@router.get("/price-lists/{pl_id}/items", response_model=list[PriceEntryOut])
def list_price_list_items(pl_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(staff)):
    _price_list(db, user.tenant_id, pl_id)
    return db.scalars(select(PriceListItem).where(PriceListItem.price_list_id == pl_id)).all()


def _upsert_prices(db: Session, user: User, model, owner_field: str, owner_id: uuid.UUID, items) -> int:
    _products_exist(db, user.tenant_id, [i.product_id for i in items])
    current = {
        r.product_id: r
        for r in db.scalars(select(model).where(getattr(model, owner_field) == owner_id, model.tenant_id == user.tenant_id))
    }
    for item in items:
        row = current.get(item.product_id)
        if row:
            if row.price != item.price:
                audit.record(db, user, model.__tablename__, row.id, "update", {"price": str(row.price)},
                             {"price": str(item.price)})
                row.price = item.price
        else:
            row = model(tenant_id=user.tenant_id, product_id=item.product_id, price=item.price,
                        **{owner_field: owner_id})
            db.add(row)
            db.flush()
            audit.record(db, user, model.__tablename__, row.id, "create", new=audit.snapshot(row))
    db.commit()
    return len(items)


@router.put("/price-lists/{pl_id}/items")
def set_price_list_items(pl_id: uuid.UUID, body: PriceEntriesIn, db: Session = Depends(get_db),
                         user: User = Depends(managers)):
    _price_list(db, user.tenant_id, pl_id)
    return {"saved": _upsert_prices(db, user, PriceListItem, "price_list_id", pl_id, body.items)}


@router.delete("/price-lists/{pl_id}/items/{product_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_price_list_item(pl_id: uuid.UUID, product_id: uuid.UUID, db: Session = Depends(get_db),
                           user: User = Depends(managers)):
    _price_list(db, user.tenant_id, pl_id)
    row = db.scalar(select(PriceListItem).where(PriceListItem.price_list_id == pl_id,
                                                PriceListItem.product_id == product_id))
    if row:
        audit.record(db, user, "price_list_items", row.id, "delete", old=audit.snapshot(row))
        db.delete(row)
        db.commit()


@router.post("/price-lists/{pl_id}/import", response_model=ImportResult)
async def import_price_list(pl_id: uuid.UUID, file: UploadFile = File(...), db: Session = Depends(get_db),
                            user: User = Depends(managers)):
    """Excel with columns sku, price."""
    _price_list(db, user.tenant_id, pl_id)
    try:
        rows = excel.read_rows(await file.read(), ["sku", "price"])
    except excel.ImportFileError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from None
    skus = {p.sku: p.id for p in db.scalars(select(Product).where(Product.tenant_id == user.tenant_id))}
    current = {r.product_id: r for r in db.scalars(select(PriceListItem).where(PriceListItem.price_list_id == pl_id))}
    errors: list[ImportRowError] = []
    created = updated = 0
    for row_no, r in rows:
        sku = (excel.as_text(r.get("sku")) or "").upper()
        errs = []
        try:
            price = _num(r.get("price"), "price")
            if price is None or price < 0:
                errs.append("price: required and must be 0 or more")
        except ValueError as exc:
            errs.append(str(exc))
            price = None
        if sku not in skus:
            errs.append(f"sku: '{sku}' not found in products")
        if errs:
            errors.append(ImportRowError(row=row_no, errors=errs))
            continue
        pid = skus[sku]
        if pid in current:
            current[pid].price = price
            updated += 1
        else:
            current[pid] = PriceListItem(tenant_id=user.tenant_id, price_list_id=pl_id, product_id=pid, price=price)
            db.add(current[pid])
            created += 1
    audit.record(db, user, "price_list_import", pl_id, "update",
                 new={"created": created, "updated": updated, "failed": len(errors)})
    db.commit()
    return ImportResult(total_rows=len(rows), created=created, updated=updated, failed=len(errors), errors=errors)


# ===================== Customer-specific prices =====================
@router.get("/customers/{customer_id}/prices", response_model=list[PriceEntryOut])
def list_customer_prices(customer_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(staff)):
    get_customer_or_404(db, user, customer_id)
    return db.scalars(select(CustomerPrice).where(CustomerPrice.customer_id == customer_id)).all()


@router.put("/customers/{customer_id}/prices")
def set_customer_prices(customer_id: uuid.UUID, body: PriceEntriesIn, db: Session = Depends(get_db),
                        user: User = Depends(managers)):
    get_customer_or_404(db, user, customer_id)
    return {"saved": _upsert_prices(db, user, CustomerPrice, "customer_id", customer_id, body.items)}


@router.delete("/customers/{customer_id}/prices/{product_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_customer_price(customer_id: uuid.UUID, product_id: uuid.UUID, db: Session = Depends(get_db),
                          user: User = Depends(managers)):
    get_customer_or_404(db, user, customer_id)
    row = db.scalar(select(CustomerPrice).where(CustomerPrice.customer_id == customer_id,
                                                CustomerPrice.product_id == product_id))
    if row:
        audit.record(db, user, "customer_prices", row.id, "delete", old=audit.snapshot(row))
        db.execute(delete(CustomerPrice).where(CustomerPrice.id == row.id))
        db.commit()


# ===================== Schemes =====================
def _scheme(db: Session, tenant_id: uuid.UUID, scheme_id: uuid.UUID) -> Scheme:
    s = db.scalar(select(Scheme).where(Scheme.id == scheme_id, Scheme.tenant_id == tenant_id))
    if s is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Scheme not found")
    return s


@router.get("/schemes", response_model=list[SchemeOut])
def list_schemes(
    current: bool | None = Query(default=None, description="true = valid today (IST) and active"),
    db: Session = Depends(get_db),
    user: User = Depends(staff),
):
    stmt = select(Scheme).where(Scheme.tenant_id == user.tenant_id)
    if current:
        today = today_ist()
        stmt = stmt.where(Scheme.is_active.is_(True), Scheme.start_date <= today, Scheme.end_date >= today)
    return db.scalars(stmt.order_by(Scheme.start_date.desc())).all()


@router.post("/schemes", response_model=SchemeOut, status_code=status.HTTP_201_CREATED)
def create_scheme(body: SchemeCreate, db: Session = Depends(get_db), user: User = Depends(managers)):
    _products_exist(db, user.tenant_id, body.product_ids)
    scheme = Scheme(tenant_id=user.tenant_id, **body.model_dump())
    db.add(scheme)
    db.flush()
    audit.record(db, user, "scheme", scheme.id, "create", new=audit.snapshot(scheme))
    db.commit()
    return scheme


@router.patch("/schemes/{scheme_id}", response_model=SchemeOut)
def update_scheme(scheme_id: uuid.UUID, body: SchemeUpdate, db: Session = Depends(get_db),
                  user: User = Depends(managers)):
    scheme = _scheme(db, user.tenant_id, scheme_id)
    data = {k: v for k, v in body.model_dump(exclude_unset=True).items() if v is not None}
    merged = {**audit.snapshot(scheme), **data}
    try:
        validate_scheme_values(
            scheme_type=data.get("scheme_type", scheme.scheme_type),
            value=Decimal(str(merged["value"])), min_qty=Decimal(str(merged["min_qty"])),
            free_qty=Decimal(str(merged["free_qty"])),
            start_date=data.get("start_date", scheme.start_date), end_date=data.get("end_date", scheme.end_date),
            target=data.get("target", scheme.target), target_values=data.get("target_values", scheme.target_values),
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from None
    if "product_ids" in data:
        if not data["product_ids"]:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Pick at least one product")
        _products_exist(db, user.tenant_id, data["product_ids"])
    old = audit.snapshot(scheme)
    for k, v in data.items():
        setattr(scheme, k, v)
    audit.record(db, user, "scheme", scheme.id, "update", old, audit.snapshot(scheme))
    db.commit()
    return scheme


# ===================== Pricing preview =====================
@router.post("/pricing/quote", response_model=QuoteOut)
def price_quote(body: QuoteIn, db: Session = Depends(get_db), user: User = Depends(staff)):
    """Price lines for a customer using the M3 rule. Used by the panel now and AI ordering (M5) later."""
    customer = get_customer_or_404(db, user, body.customer_id)
    ids = [l.product_id for l in body.lines]
    products = {p.id: p for p in db.scalars(select(Product).where(Product.tenant_id == user.tenant_id,
                                                                   Product.id.in_(ids)))}
    missing = [str(i) for i in ids if i not in products]
    if missing:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Unknown product ids: {', '.join(missing)}")
    engine = PricingEngine(db, customer, body.on_date)
    return engine.quote([(products[l.product_id], l.quantity) for l in body.lines])
