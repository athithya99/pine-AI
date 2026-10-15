"""Management commands.

    python -m app.cli create-superadmin --name "Pinesphere Admin" --email admin@pinesphere.in --password '...'
    python -m app.cli seed-demo     # demo company with users, customers and products
"""

import argparse
import sys
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import select

from app.core.database import SessionLocal
from app.core.security import hash_password
from app.models import (
    Customer,
    CustomerType,
    MessageTemplate,
    TemplateCategory,
    TemplateStatus,
    WaMode,
    WhatsAppAccount,
    PriceList,
    PriceListItem,
    Product,
    Role,
    Scheme,
    SchemeType,
    StockStatus,
    Tenant,
    User,
)
from app.services.pricing import today_ist
from app.services.tenants import seed_tenant_defaults


def create_superadmin(name: str, email: str, password: str) -> None:
    with SessionLocal() as db:
        if db.scalar(select(User).where(User.email == email)):
            print(f"User {email} already exists")
            return
        db.add(User(name=name, email=email, password_hash=hash_password(password), role=Role.super_admin))
        db.commit()
        print(f"Super admin {email} created")


def seed_demo() -> None:
    with SessionLocal() as db:
        if db.scalar(select(Tenant).where(Tenant.name == "Demo Traders")):
            print("Demo data already exists")
            return
        t = Tenant(name="Demo Traders", gstin="33AABCD1234E1Z5", state="Tamil Nadu",
                   billing_address="1 Avinashi Road, Coimbatore 641018", contact_email="demo@example.com")
        db.add(t)
        db.flush()
        seed_tenant_defaults(db, t)
        pw = hash_password("Demo@1234")
        owner = User(tenant_id=t.id, name="Demo Owner", email="owner@demotraders.in", mobile="+919800000001",
                     password_hash=pw, role=Role.owner)
        sales = User(tenant_id=t.id, name="Ravi (Sales)", email="sales@demotraders.in", mobile="+919800000002",
                     password_hash=pw, role=Role.sales)
        accts = User(tenant_id=t.id, name="Priya (Accounts)", email="accounts@demotraders.in", mobile="+919800000003",
                     password_hash=pw, role=Role.accounts)
        db.add_all([owner, sales, accts])

        wholesale = PriceList(tenant_id=t.id, name="Wholesale", description="Distributors and large retailers")
        db.add(wholesale)
        db.flush()

        products = [
            ("SALT-1KG", "Tata Salt 1kg", "Tata", "Staples", "PCS", "1 kg", "2501", 5, 28, 24, ["salt", "uppu"]),
            ("ATTA-5KG", "Aashirvaad Atta 5kg", "Aashirvaad", "Staples", "BAG", "5 kg", "1101", 5, 290, 255,
             ["atta", "wheat flour", "godhumai maavu"]),
            ("OIL-SUN-1L", "Sunflower Oil 1L Pouch", "Gold Winner", "Edible Oil", "PCS", "1 L", "1512", 5, 175, 150,
             ["sunflower oil", "oil pouch"]),
            ("SOAP-100", "Bath Soap 100g", "Medimix", "Personal Care", "PCS", "100 g", "3401", 5, 45, 38,
             ["soap", "medimix"]),
            ("DET-1KG", "Detergent Powder 1kg", "Surf Excel", "Home Care", "PCS", "1 kg", "3402", 18, 140, 118,
             ["surf", "washing powder"]),
        ]
        rows = []
        for sku, name, brand, cat, uom, pack, hsn, gst, mrp, price, aliases in products:
            p = Product(tenant_id=t.id, sku=sku, name=name, brand=brand, category=cat, uom=uom, pack_size=pack,
                        hsn_code=hsn, gst_rate=Decimal(gst), mrp=Decimal(mrp), base_price=Decimal(price),
                        aliases=aliases)
            db.add(p)
            rows.append(p)
        db.flush()
        rows[4].stock_status = StockStatus.low
        for p in rows:
            db.add(PriceListItem(tenant_id=t.id, price_list_id=wholesale.id, product_id=p.id,
                                 price=(p.base_price * Decimal("0.95")).quantize(Decimal("0.01"))))
        today = today_ist()
        db.add(Scheme(tenant_id=t.id, name="Atta: buy 10 get 1", scheme_type=SchemeType.free_qty,
                      min_qty=Decimal(10), free_qty=Decimal(1), start_date=today,
                      end_date=today + timedelta(days=30), product_ids=[rows[1].id]))

        customers = [
            ("Sri Murugan Stores", "+919811111111", CustomerType.retailer, ["coimbatore"], None),
            ("Lakshmi Agencies", "+919822222222", CustomerType.distributor, ["coimbatore", "wholesale"], wholesale.id),
            ("Kumar Provisions", "+919833333333", CustomerType.retailer, ["tiruppur"], None),
        ]
        for name, num, ctype, tags, pl in customers:
            db.add(Customer(tenant_id=t.id, business_name=name, whatsapp_number=num, customer_type=ctype, tags=tags,
                            price_list_id=pl, assigned_user_id=sales.id, credit_limit=Decimal(50000),
                            credit_days=30, whatsapp_opt_in=True, opt_in_source="seed"))
        # WhatsApp in simulator mode with one approved template, so the inbox works without Meta
        db.add(WhatsAppAccount(tenant_id=t.id, mode=WaMode.simulator, phone_number_id="sim100000000001",
                               display_phone_number="Simulator"))
        db.add(MessageTemplate(
            tenant_id=t.id, name="order_update", language="en", category=TemplateCategory.utility,
            body="Hi {{1}}, there is an update on your order {{2}}. Reply here if you have any questions.",
            example_values=["Murugan", "ORD/26-27/00001"], variable_count=2, status=TemplateStatus.approved,
            buttons=[{"type": "QUICK_REPLY", "text": "Talk to us"}],
        ))
        db.commit()
        print("Demo data created. Logins (password Demo@1234): owner@demotraders.in, sales@demotraders.in, accounts@demotraders.in")


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sa = sub.add_parser("create-superadmin")
    sa.add_argument("--name", required=True)
    sa.add_argument("--email", required=True)
    sa.add_argument("--password", required=True)
    sub.add_parser("seed-demo")
    args = parser.parse_args()
    if args.cmd == "create-superadmin":
        if len(args.password) < 8:
            sys.exit("Password must be at least 8 characters")
        create_superadmin(args.name, args.email, args.password)
    elif args.cmd == "seed-demo":
        seed_demo()


if __name__ == "__main__":
    main()
