"""M3 acceptance criteria and pricing rules."""

from datetime import timedelta
from decimal import Decimal

from app.models import Role
from app.services.pricing import today_ist
from tests.test_m2_customers import upload, xlsx

API = "/api/v1"


def product(client, h, sku, price, gst=5, **extra):
    r = client.post(f"{API}/products", headers=h,
                    json={"sku": sku, "name": f"Product {sku}", "base_price": price, "gst_rate": gst, **extra})
    assert r.status_code == 201, r.text
    return r.json()


def customer(client, h, number="9876500001", **extra):
    r = client.post(f"{API}/customers", headers=h, json={"business_name": "Shop", "whatsapp_number": number, **extra})
    assert r.status_code == 201, r.text
    return r.json()


def quote(client, h, customer_id, lines, on_date=None):
    body = {"customer_id": customer_id, "lines": [{"product_id": p, "quantity": q} for p, q in lines]}
    if on_date:
        body["on_date"] = on_date
    r = client.post(f"{API}/pricing/quote", headers=h, json=body)
    assert r.status_code == 200, r.text
    return r.json()


def test_price_priority_override_then_price_list_then_base(client, company_a):
    """AC: override on SKU A, Wholesale price for other items."""
    h = company_a.h(Role.owner)
    a = product(client, h, "A", 100)
    b = product(client, h, "B", 200)
    c = product(client, h, "C", 300)
    pl = client.post(f"{API}/price-lists", headers=h, json={"name": "Wholesale"}).json()
    client.put(f"{API}/price-lists/{pl['id']}/items", headers=h,
               json={"items": [{"product_id": a["id"], "price": 90}, {"product_id": b["id"], "price": 180}]})
    cust = customer(client, h, price_list_id=pl["id"])
    client.put(f"{API}/customers/{cust['id']}/prices", headers=h, json={"items": [{"product_id": a["id"], "price": 85}]})

    q = quote(client, h, cust["id"], [(a["id"], 1), (b["id"], 1), (c["id"], 1)])
    by_sku = {l["sku"]: l for l in q["lines"]}
    assert (by_sku["A"]["unit_price"], by_sku["A"]["price_source"]) == ("85.00", "customer_price")
    assert (by_sku["B"]["unit_price"], by_sku["B"]["price_source"]) == ("180.00", "price_list")
    assert (by_sku["C"]["unit_price"], by_sku["C"]["price_source"]) == ("300.00", "base_price")


def test_gst_and_totals(client, company_a):
    h = company_a.h(Role.owner)
    p5 = product(client, h, "G5", "99.99", gst=5)
    p18 = product(client, h, "G18", 50, gst=18)
    cust = customer(client, h)
    q = quote(client, h, cust["id"], [(p5["id"], 3), (p18["id"], 2)])
    # 3 x 99.99 = 299.97, GST 5% = 15.00 (14.9985 rounded); 2 x 50 = 100, GST 18 = 18.00
    assert q["taxable_total"] == "399.97"
    assert q["gst_total"] == "33.00"
    assert q["grand_total"] == "432.97"


def test_expired_scheme_never_applied(client, company_a):
    """AC: an expired scheme is not applied, even just after midnight on its end date."""
    h = company_a.h(Role.owner)
    p = product(client, h, "S1", 100)
    cust = customer(client, h)
    today = today_ist()
    client.post(f"{API}/schemes", headers=h, json={
        "name": "10% ended yesterday", "scheme_type": "percent", "value": 10,
        "start_date": str(today - timedelta(days=10)), "end_date": str(today - timedelta(days=1)),
        "product_ids": [p["id"]]})
    q = quote(client, h, cust["id"], [(p["id"], 5)])
    assert q["lines"][0]["discount"] == "0.00" and q["lines"][0]["scheme_id"] is None
    # on its last day it still applies (end date is inclusive)
    q = quote(client, h, cust["id"], [(p["id"], 5)], on_date=str(today - timedelta(days=1)))
    assert q["lines"][0]["discount"] == "50.00"
    # but uses IST "today" by default, so the day after end date gets nothing
    assert client.get(f"{API}/schemes?current=true", headers=h).json() == []


def test_best_single_scheme_no_stacking(client, company_a):
    h = company_a.h(Role.owner)
    p = product(client, h, "BS", 100)
    cust = customer(client, h, tags=["vip"])
    today = str(today_ist())
    common = {"start_date": today, "end_date": today, "product_ids": [p["id"]]}
    client.post(f"{API}/schemes", headers=h, json={"name": "5%", "scheme_type": "percent", "value": 5, **common})
    client.post(f"{API}/schemes", headers=h, json={"name": "Rs 8 off", "scheme_type": "flat_per_unit", "value": 8,
                                                     "min_qty": 10, **common})
    client.post(f"{API}/schemes", headers=h, json={"name": "VIP 20%", "scheme_type": "percent", "value": 20,
                                                     "target": "tags", "target_values": ["gold"], **common})
    # qty 5: only 5% applies (flat needs 10, VIP needs tag gold)
    line = quote(client, h, cust["id"], [(p["id"], 5)])["lines"][0]
    assert line["scheme_name"] == "5%" and line["discount"] == "25.00"
    # qty 10: Rs 8 x 10 = 80 beats 5% = 50; they do not stack
    line = quote(client, h, cust["id"], [(p["id"], 10)])["lines"][0]
    assert line["scheme_name"] == "Rs 8 off" and line["discount"] == "80.00"


def test_free_quantity_scheme(client, company_a):
    h = company_a.h(Role.owner)
    p = product(client, h, "FQ", 250)
    cust = customer(client, h)
    today = str(today_ist())
    client.post(f"{API}/schemes", headers=h, json={
        "name": "Buy 10 get 1", "scheme_type": "free_qty", "min_qty": 10, "free_qty": 1,
        "start_date": today, "end_date": today, "product_ids": [p["id"]]})
    line = quote(client, h, cust["id"], [(p["id"], 25)])["lines"][0]
    assert Decimal(line["free_qty"]) == 2
    assert line["taxable_value"] == "6250.00"  # free goods are not charged, paid qty unchanged
    assert Decimal(quote(client, h, cust["id"], [(p["id"], 9)])["lines"][0]["free_qty"]) == 0


def test_scheme_targets_customer_type(client, company_a):
    h = company_a.h(Role.owner)
    p = product(client, h, "CT", 100)
    dist = customer(client, h, "9876500001", customer_type="distributor")
    shop = customer(client, h, "9876500002", customer_type="retailer")
    today = str(today_ist())
    client.post(f"{API}/schemes", headers=h, json={
        "name": "Distributor 3%", "scheme_type": "percent", "value": 3, "target": "customer_types",
        "target_values": ["distributor"], "start_date": today, "end_date": today, "product_ids": [p["id"]]})
    assert quote(client, h, dist["id"], [(p["id"], 1)])["lines"][0]["discount"] == "3.00"
    assert quote(client, h, shop["id"], [(p["id"], 1)])["lines"][0]["discount"] == "0.00"


def test_out_of_stock_flagged(client, company_a):
    """AC: out-of-stock product is flagged in the quote."""
    h = company_a.h(Role.owner)
    p = product(client, h, "OOS", 100)
    client.put(f"{API}/products/{p['id']}/stock", headers=h, json={"stock_status": "out_of_stock"})
    cust = customer(client, h)
    q = quote(client, h, cust["id"], [(p["id"], 1)])
    assert q["has_out_of_stock"] is True and q["lines"][0]["stock_status"] == "out_of_stock"


def test_gst_rate_must_be_in_master(client, company_a):
    h = company_a.h(Role.owner)
    r = client.post(f"{API}/products", headers=h, json={"sku": "X", "name": "X", "base_price": 10, "gst_rate": 12})
    assert r.status_code == 400  # 12% slab removed in Sept 2025, not in default master
    client.post(f"{API}/gst-rates", headers=h, json={"rate": 12})
    r = client.post(f"{API}/products", headers=h, json={"sku": "X", "name": "X", "base_price": 10, "gst_rate": 12})
    assert r.status_code == 201


def test_product_import_creates_and_updates(client, company_a):
    h = company_a.h(Role.owner)
    product(client, h, "OLD-1", 10, brand="KeepMe")
    content = xlsx(
        ["sku", "name", "base_price", "gst_rate", "brand", "aliases", "stock_status"],
        [
            ["old-1", "Old One Renamed", 12, 5, None, "old, one", None],   # update, brand untouched
            ["NEW-1", "New One", 20, 18, "Acme", None, "low"],               # create
            ["NEW-2", "Bad Rate", 20, 12, None, None, None],                  # 12% not in master
            ["NEW-1", "Dup", 20, 5, None, None, None],                         # duplicate in file
            ["NEW-3", "No Price", None, 5, None, None, None],                  # missing price
        ],
    )
    r = upload(client, h, f"{API}/products/import", content).json()
    assert (r["created"], r["updated"], r["failed"]) == (1, 1, 3)
    assert [e["row"] for e in r["errors"]] == [4, 5, 6]
    old = client.get(f"{API}/products?q=OLD-1", headers=h).json()["items"][0]
    assert old["name"] == "Old One Renamed" and old["base_price"] == "12.00" and old["brand"] == "KeepMe"
    assert old["aliases"] == ["old", "one"]


def test_search_by_alias(client, company_a):
    h = company_a.h(Role.owner)
    product(client, h, "ATTA", 250, aliases=["godhumai maavu"])
    assert client.get(f"{API}/products?q=maavu", headers=h).json()["total"] == 1


def test_price_list_import(client, company_a):
    h = company_a.h(Role.owner)
    product(client, h, "P1", 100)
    pl = client.post(f"{API}/price-lists", headers=h, json={"name": "Retail"}).json()
    r = upload(client, h, f"{API}/price-lists/{pl['id']}/import",
               xlsx(["sku", "price"], [["p1", 95], ["NOPE", 10], ["P1", "abc"]])).json()
    assert r["created"] == 1 and r["failed"] == 2


def test_catalog_permissions(client, company_a):
    p = product(client, company_a.h(Role.owner), "PERM", 10)
    sales = company_a.h(Role.sales)
    assert client.get(f"{API}/products", headers=sales).status_code == 200
    assert client.patch(f"{API}/products/{p['id']}", headers=sales, json={"base_price": 1}).status_code == 403


def test_sku_unique_and_cross_tenant(client, company_a, company_b):
    pa = product(client, company_a.h(Role.owner), "SAME", 10)
    assert client.post(f"{API}/products", headers=company_a.h(Role.owner),
                       json={"sku": "same", "name": "x", "base_price": 1, "gst_rate": 5}).status_code == 409
    product(client, company_b.h(Role.owner), "SAME", 10)  # allowed in another tenant
    assert client.get(f"{API}/products/{pa['id']}", headers=company_b.h(Role.owner)).status_code == 404


def test_scheme_validation(client, company_a):
    h = company_a.h(Role.owner)
    p = product(client, h, "SV", 10)
    today = str(today_ist())
    bad = client.post(f"{API}/schemes", headers=h, json={
        "name": "bad", "scheme_type": "percent", "value": 150, "start_date": today, "end_date": today,
        "product_ids": [p["id"]]})
    assert bad.status_code == 422
    bad = client.post(f"{API}/schemes", headers=h, json={
        "name": "bad", "scheme_type": "free_qty", "start_date": today, "end_date": today, "product_ids": [p["id"]]})
    assert bad.status_code == 422
