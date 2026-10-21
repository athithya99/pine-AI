"""M7 acceptance criteria: GST split, ledger balance, invoice rules, uploads, WhatsApp statement."""

from datetime import date, timedelta
from decimal import Decimal
from io import BytesIO
from types import SimpleNamespace

import pytest
from pypdf import PdfReader
from sqlalchemy import select

from app.models import Direction, Invoice, LedgerEntry, Message, Role
from app.services import pdfs
from app.services.gst import amount_in_words, is_inter_state, state_name
from app.services.invoices import today
from app.services.orders import financial_year
from tests.test_m2_customers import upload, xlsx
from tests.test_m3_catalog import customer, product
from tests.test_m4_whatsapp import connect_simulator, sim_text

API = "/api/v1"
SELLER_GSTIN = "33AABCD1234E1Z5"   # Tamil Nadu
KA_GSTIN = "29AAACK1234L1Z2"       # Karnataka


@pytest.fixture
def biz(client, company_a):
    h = company_a.h(Role.owner)
    r = client.patch(f"{API}/company", headers=h, json={"gstin": SELLER_GSTIN, "state": "Tamil Nadu"})
    assert r.status_code == 200, r.text
    p = product(client, h, "OIL-1L", 150, gst=18, name="Sunflower Oil 1L", hsn_code="1512")
    return SimpleNamespace(client=client, h=h, p=p, company=company_a)


def approved_order(b, cust_id, qty=10):
    c, h = b.client, b.h
    r = c.post(f"{API}/orders", headers=h, json={"customer_id": cust_id, "action": "confirm",
                                                  "lines": [{"product_id": b.p["id"], "quantity": qty}]})
    assert r.status_code == 201, r.text
    o = r.json()
    r = c.post(f"{API}/orders/{o['id']}/approve", headers=h, json={})
    assert r.status_code == 200, r.text
    return r.json()


def make_invoice(b, order, body=None, expect=200):
    r = b.client.post(f"{API}/orders/{order['id']}/invoice", headers=b.h, json=body or {"mode": "generate"})
    assert r.status_code == expect, r.text
    return r.json()


def inv_of(b, order_json):
    return b.client.get(f"{API}/invoices/{order_json['invoice_id']}", headers=b.h).json()


def ledger(b, cust_id, **params):
    r = b.client.get(f"{API}/customers/{cust_id}/ledger", headers=b.h, params=params)
    assert r.status_code == 200, r.text
    return r.json()


def test_helpers():
    assert amount_in_words(Decimal("1250000.50")) == "Rupees Twelve Lakh Fifty Thousand and Fifty Paise Only"
    assert amount_in_words(Decimal("1770")) == "Rupees One Thousand Seven Hundred Seventy Only"
    assert state_name("29AAACK1234L1Z2", "Tamil Nadu") == "Karnataka"
    assert is_inter_state("Tamil Nadu", "Karnataka") and not is_inter_state("tamil nadu", "TN")


def test_intra_state_invoice_splits_cgst_sgst(biz):
    """AC: intra-state shows CGST and SGST at half the GST rate each."""
    c = customer(biz.client, biz.h, state="Tamil Nadu", credit_days=30)
    o = make_invoice(biz, approved_order(biz, c["id"]))
    assert o["status"] == "invoiced" and o["invoice_id"]
    inv = inv_of(biz, o)
    fy = financial_year(today())
    assert inv["number"] == f"INV/{fy}/0001" and len(inv["number"]) <= 16
    assert inv["source"] == "generated" and inv["status"] == "issued" and not inv["inter_state"]
    assert inv["taxable_value"] == "1500.00" and inv["cgst"] == "135.00" and inv["sgst"] == "135.00"
    assert inv["igst"] == "0.00" and inv["total"] == "1770.00"
    line = inv["lines"][0]
    assert line["hsn_code"] == "1512" and line["cgst"] == "135.00" and line["gst_rate"] == "18.00"
    assert inv["due_date"] == (date.fromisoformat(inv["invoice_date"]) + timedelta(days=30)).isoformat()
    assert inv["amount_in_words"] == "Rupees One Thousand Seven Hundred Seventy Only"
    pdf = biz.client.get(f"{API}/invoices/{inv['id']}/pdf", headers=biz.h)
    text = "\n".join(p.extract_text() for p in PdfReader(BytesIO(pdf.content)).pages)
    assert "TAX INVOICE" in text and "CGST" in text and SELLER_GSTIN in text and "1,770.00" in text
    st = ledger(biz, c["id"])
    assert st["entries"][0]["debit"] == "1770.00" and st["closing"] == "1770.00"


def test_inter_state_invoice_uses_igst(biz):
    """AC: inter-state shows IGST at the full rate."""
    c = customer(biz.client, biz.h, gstin=KA_GSTIN, state="Karnataka")
    inv = inv_of(biz, make_invoice(biz, approved_order(biz, c["id"])))
    assert inv["inter_state"] and inv["place_of_supply"] == "Karnataka"
    assert inv["igst"] == "270.00" and inv["cgst"] == "0.00" and inv["total"] == "1770.00"


def test_short_supply_and_round_off(biz):
    c = customer(biz.client, biz.h)
    p2 = product(biz.client, biz.h, "SOAP", 33.33, gst=5)
    r = biz.client.post(f"{API}/orders", headers=biz.h, json={"customer_id": c["id"], "action": "confirm", "lines": [
        {"product_id": biz.p["id"], "quantity": 10}, {"product_id": p2["id"], "quantity": 3}]})
    o = biz.client.post(f"{API}/orders/{r.json()['id']}/approve", headers=biz.h, json={}).json()
    oil = next(l for l in o["lines"] if l["sku"] == "OIL-1L")
    o = make_invoice(biz, o, {"mode": "generate", "lines": [{"line_id": oil["id"], "invoiced_qty": 4}]})
    inv = inv_of(biz, o)
    assert [l["quantity"] for l in inv["lines"]] == ["4.000", "3.000"]
    # 600 + 99.99 taxable; tax 108 + 5.00 -> 812.99 -> rounded to 813
    assert inv["taxable_value"] == "699.99" and inv["total"] == "813.00" and inv["round_off"] == "0.01"


def test_generate_needs_company_gstin(client, company_a):
    h = company_a.h(Role.owner)
    p = product(client, h, "A", 10)
    c = customer(client, h)
    b = SimpleNamespace(client=client, h=h, p=p)
    r = client.post(f"{API}/orders/{approved_order(b, c['id'])['id']}/invoice", headers=h, json={"mode": "generate"})
    assert r.status_code == 400 and "GSTIN" in r.json()["detail"]


def test_erp_invoice_number_attach_pdf_and_duplicates(biz):
    c = customer(biz.client, biz.h)
    o = make_invoice(biz, approved_order(biz, c["id"]), {"invoice_number": "TI/2026/77"})
    inv = inv_of(biz, o)
    assert inv["source"] == "erp" and inv["number"] == "TI/2026/77" and not inv["has_pdf"]
    o2 = approved_order(biz, c["id"])
    r = biz.client.post(f"{API}/orders/{o2['id']}/invoice", headers=biz.h, json={"invoice_number": "ti/2026/77"})
    assert r.status_code == 400 and "already used" in r.json()["detail"]
    r = biz.client.post(f"{API}/invoices/{inv['id']}/pdf", headers=biz.h,
                        files={"file": ("x.pdf", b"%PDF-1.4 test", "application/pdf")})
    assert r.status_code == 200 and r.json()["has_pdf"]


def test_cancel_reverses_ledger_and_reopens_order(biz):
    """Business rule: an invoice is never edited, only cancelled; cancellation reverses its ledger entry."""
    c = customer(biz.client, biz.h)
    o = make_invoice(biz, approved_order(biz, c["id"]))
    inv = inv_of(biz, o)
    assert biz.client.post(f"{API}/invoices/{inv['id']}/cancel", headers=biz.h, json={"reason": ""}).status_code == 422
    r = biz.client.post(f"{API}/invoices/{inv['id']}/cancel", headers=biz.h, json={"reason": "Wrong rate"})
    assert r.status_code == 200 and r.json()["status"] == "cancelled"
    st = ledger(biz, c["id"])
    assert [e["kind"] for e in st["entries"]] == ["invoice", "invoice_cancelled"] and st["closing"] == "0.00"
    order = biz.client.get(f"{API}/orders/{o['id']}", headers=biz.h).json()
    assert order["status"] == "approved" and order["invoice_id"] is None
    o = make_invoice(biz, order)  # can invoice again with a new number
    inv2 = inv_of(biz, o)
    assert inv2["number"].endswith("0002")
    biz.client.post(f"{API}/orders/{o['id']}/dispatch", headers=biz.h, json={})
    r = biz.client.post(f"{API}/invoices/{inv2['id']}/cancel", headers=biz.h, json={"reason": "late change"})
    assert r.status_code == 409 and "credit note" in r.json()["detail"]


def test_ledger_closing_balance_rule(biz):
    """AC: closing = opening + invoices - payments - credit notes."""
    c = customer(biz.client, biz.h, opening_balance=1000)
    inv1 = inv_of(biz, make_invoice(biz, approved_order(biz, c["id"], qty=10)))   # 1770
    inv_of(biz, make_invoice(biz, approved_order(biz, c["id"], qty=5)))           # 885
    r = biz.client.post(f"{API}/credit-notes", headers=biz.h,
                        json={"customer_id": c["id"], "invoice_id": inv1["id"], "amount": 270, "reason": "Damaged"})
    assert r.status_code == 201, r.text
    cn = r.json()
    assert cn["number"].startswith("CN/") and cn["invoice_number"] == inv1["number"]
    st = ledger(biz, c["id"])
    assert st["opening"] == "1000.00" and st["closing"] == str(Decimal("1000") + 1770 + 885 - 270) + ".00"
    assert [e["balance"] for e in st["entries"]] == ["2770.00", "3655.00", "3385.00"]
    # too much credit against one invoice
    r = biz.client.post(f"{API}/credit-notes", headers=biz.h,
                        json={"customer_id": c["id"], "invoice_id": inv1["id"], "amount": 1600, "reason": "x y"})
    assert r.status_code == 400
    biz.client.post(f"{API}/credit-notes/{cn['id']}/cancel", headers=biz.h, json={"reason": "issued by mistake"})
    assert ledger(biz, c["id"])["closing"] == "3655.00"
    # a period after today starts from the full balance
    later = (today() + timedelta(days=1)).isoformat()
    assert ledger(biz, c["id"], date_from=later, date_to=later)["opening"] == "3655.00"
    pdf = biz.client.get(f"{API}/customers/{c['id']}/ledger.pdf", headers=biz.h)
    assert pdf.status_code == 200 and b"%PDF" in pdf.content[:5]


def test_credit_exposure_uses_ledger(biz):
    c = customer(biz.client, biz.h, credit_limit=3000)
    make_invoice(biz, approved_order(biz, c["id"]))  # 1770 in ledger
    r = biz.client.post(f"{API}/orders", headers=biz.h, json={"customer_id": c["id"], "action": "confirm",
                                                               "lines": [{"product_id": biz.p["id"], "quantity": 10}]})
    assert r.json()["status"] == "on_hold" and r.json()["credit"]["outstanding"] == "1770.00"


def test_uploaded_invoice_read_reviewed_then_saved(biz, db):
    """FR-M7-01 + rule: uploaded invoices are not shared or in the ledger until staff confirm."""
    client, h = biz.client, biz.h
    connect_simulator(client, h)
    c = customer(client, h, business_name="Kumar Provisions", gstin="33AAACK9999A1Z5")
    sim_text(client, h, "9876500001", "hi")
    tenant = SimpleNamespace(name="Demo Traders", billing_address="Coimbatore", gstin=SELLER_GSTIN, state="Tamil Nadu")
    fake = SimpleNamespace(
        number="TI/2026/0456", invoice_date=date(2026, 10, 10), due_date=date(2026, 11, 9),
        buyer_gstin="33AAACK9999A1Z5", place_of_supply="Tamil Nadu", inter_state=False, notes=None,
        lines=[SimpleNamespace(description="Oil", free_qty=0, uom="PCS", hsn_code="1512", quantity=Decimal(2),
                               rate=Decimal("1000"), discount=Decimal(0), taxable_value=Decimal("2000"),
                               gst_rate=Decimal(5), cgst=Decimal(50), sgst=Decimal(50), igst=Decimal(0),
                               total=Decimal(2100))],
        taxable_value=Decimal("2000.00"), cgst=Decimal("50.00"), sgst=Decimal("50.00"), igst=Decimal(0),
        round_off=Decimal(0), total=Decimal("2100.00"))
    buyer = SimpleNamespace(business_name="Kumar Provisions", billing_address="Gandhipuram", shipping_address=None,
                            whatsapp_number="+919876500001")
    content = pdfs.invoice_pdf(fake, tenant, buyer)
    r = client.post(f"{API}/invoices/upload", headers=h, files=[("files", ("TI-2026-0456.pdf", content, "application/pdf"))])
    assert r.status_code == 200, r.text
    inv = client.get(f"{API}/invoices/{r.json()[0]['id']}", headers=h).json()
    ai = inv["ai_extracted"]
    assert inv["status"] == "review" and ai["invoice_number"] == "TI/2026/0456" and ai["total"] == "2100.00"
    assert ai["invoice_date"] == "2026-10-10" and ai["customer_id"] == c["id"] and ai["tax_amount"] == "100.00"
    assert ledger(biz, c["id"])["entries"] == []
    assert client.post(f"{API}/invoices/{inv['id']}/send", headers=h, json={}).status_code == 400
    before = db.scalar(select(Message.id).where(Message.direction == Direction.outbound).order_by(Message.created_at.desc()))
    r = client.post(f"{API}/invoices/{inv['id']}/confirm", headers=h, json={
        "invoice_number": ai["invoice_number"], "invoice_date": ai["invoice_date"], "customer_id": c["id"],
        "taxable_value": ai["taxable_value"] or "2000", "tax_amount": ai["tax_amount"], "total": ai["total"]})
    assert r.status_code == 200, r.text
    saved = r.json()
    assert saved["status"] == "issued" and saved["cgst"] == "50.00" and saved["sent"]
    assert ledger(biz, c["id"])["closing"] == "2100.00"
    db.expire_all()
    last = db.scalars(select(Message).where(Message.direction == Direction.outbound).order_by(Message.created_at.desc())).first()
    assert last.id != before and last.msg_type.value == "document" and last.media_filename == "TI-2026-0456.pdf"
    # discard works only for review; saved invoices are cancelled instead
    assert client.delete(f"{API}/invoices/{inv['id']}", headers=h).status_code == 409


def test_excel_import_and_attach_pdfs(biz):
    client, h = biz.client, biz.h
    customer(client, h)
    customer(client, h, number="9876500002", gstin=KA_GSTIN, business_name="KA Stores")
    content = xlsx(["invoice_number", "invoice_date", "customer_whatsapp", "customer_gstin", "taxable_value",
                    "tax_amount", "total", "due_date"], [
        ["TI/1", "01-10-2026", "9876500001", None, 1000, 50, 1050, None],
        ["TI/2", "02-10-2026", None, KA_GSTIN, 2000, 360, 2360, "30-10-2026"],
        ["TI/1", "03-10-2026", "9876500001", None, 10, 0, 10, None],
        ["TI/3", "bad date", "9999999999", None, None, None, 0, None],
    ])
    r = upload(client, h, f"{API}/invoices/import", content)
    assert r.status_code == 200, r.text
    res = r.json()
    assert res["created"] == 2 and {e["row"] for e in res["errors"]} == {4, 5}
    invs = {i["number"]: i for i in client.get(f"{API}/invoices", headers=h).json()["items"]}
    ka = client.get(f"{API}/invoices/{invs['TI/2']['id']}", headers=h).json()
    assert ka["inter_state"] and ka["igst"] == "360.00" and ka["due_date"] == "2026-10-30"
    r = client.post(f"{API}/invoices/attach-pdfs", headers=h, files=[
        ("files", ("TI-1.pdf", b"%PDF-1.4 a", "application/pdf")), ("files", ("TI-9.pdf", b"%PDF-1.4 b", "application/pdf"))])
    assert r.json() == {"matched": ["TI/1"], "unmatched": ["TI-9.pdf"]}
    exp = client.get(f"{API}/invoices/export", headers=h)
    assert exp.status_code == 200


def test_opening_balance_import_and_balances(biz):
    client, h = biz.client, biz.h
    c = customer(client, h)
    content = xlsx(["customer_whatsapp", "opening_balance"], [["9876500001", "12,500"], ["9999999999", 10]])
    res = upload(client, h, f"{API}/ledger/opening-balances/import", content).json()
    assert res["updated"] == 1 and res["failed"] == 1
    assert ledger(biz, c["id"])["opening"] == "12500.00"
    bals = client.get(f"{API}/ledger/balances", headers=h, params={"only_due": True}).json()
    assert bals[0]["balance"] == "12500.00"


def test_whatsapp_statement_and_invoice_copy(biz, db):
    """FR-M7-09 / FR-M7-11."""
    client, h = biz.client, biz.h
    connect_simulator(client, h)
    c = customer(client, h)
    sim_text(client, h, "9876500001", "hi")
    inv = inv_of(biz, make_invoice(biz, approved_order(biz, c["id"])))

    def last():
        db.expire_all()
        return db.scalars(select(Message).where(Message.customer_id == c["id"], Message.direction == Direction.outbound)
                          .order_by(Message.created_at.desc())).first()
    sim_text(client, h, "9876500001", "send ledger")
    m = last()
    assert m.msg_type.value == "document" and m.media_filename.startswith("Statement-") and "₹1,770.00" in m.text
    sim_text(client, h, "9876500001", f"send invoice {inv['number']}")
    m = last()
    assert m.msg_type.value == "document" and inv["number"] in m.text
    sim_text(client, h, "9876500001", "invoice ZZ999 copy")
    assert "couldn't find invoice" in last().text


def test_invoice_pdf_sent_with_invoiced_status_once(biz, db):
    """M6 rule still holds: the Invoiced update is one message, now carrying the PDF."""
    client, h = biz.client, biz.h
    connect_simulator(client, h)
    c = customer(client, h)
    sim_text(client, h, "9876500001", "hi")
    o = approved_order(biz, c["id"])
    db.expire_all()
    n = len(db.scalars(select(Message).where(Message.customer_id == c["id"], Message.direction == Direction.outbound)).all())
    o = make_invoice(biz, o)
    db.expire_all()
    out = db.scalars(select(Message).where(Message.customer_id == c["id"], Message.direction == Direction.outbound)
                     .order_by(Message.created_at)).all()
    assert len(out) == n + 1 and out[-1].msg_type.value == "document" and "invoiced" in out[-1].text
    ev = [e for e in o["events"] if e["to_status"] == "invoiced"][0]
    assert ev["message_id"] == str(out[-1].id)
    # auto-send off -> plain status text instead
    client.patch(f"{API}/company", headers=h, json={"invoice_auto_send": False})
    make_invoice(biz, approved_order(biz, c["id"]))
    db.expire_all()
    assert db.scalars(select(Message).where(Message.customer_id == c["id"]).order_by(Message.created_at.desc())).first().msg_type.value == "text"


def test_permissions_and_isolation(biz, company_b):
    client, h = biz.client, biz.h
    sales_user = biz.company.users[Role.sales]
    mine = customer(client, h, assigned_user_id=str(sales_user.id))
    other = customer(client, h, number="9876500009")
    inv_m = inv_of(biz, make_invoice(biz, approved_order(biz, mine["id"])))
    inv_o = inv_of(biz, make_invoice(biz, approved_order(biz, other["id"])))
    sales, accts = biz.company.h(Role.sales), biz.company.h(Role.accounts)
    assert [i["id"] for i in client.get(f"{API}/invoices", headers=sales).json()["items"]] == [inv_m["id"]]
    assert client.get(f"{API}/invoices/{inv_o['id']}", headers=sales).status_code == 404
    assert client.post(f"{API}/credit-notes", headers=sales, json={
        "customer_id": mine["id"], "amount": 10, "reason": "x y"}).status_code == 403
    assert client.post(f"{API}/credit-notes", headers=accts, json={
        "customer_id": mine["id"], "amount": 10, "reason": "goodwill"}).status_code == 201
    assert client.get(f"{API}/invoices/{inv_m['id']}", headers=company_b.h(Role.owner)).status_code == 404
    assert client.get(f"{API}/customers/{mine['id']}/ledger", headers=company_b.h(Role.owner)).status_code == 404


def test_invoice_prefix_setting(biz, db):
    biz.client.patch(f"{API}/company", headers=biz.h, json={"invoice_prefix": "ps"})
    assert biz.client.patch(f"{API}/company", headers=biz.h, json={"invoice_prefix": "TOOLONG"}).status_code == 422
    c = customer(biz.client, biz.h)
    inv = inv_of(biz, make_invoice(biz, approved_order(biz, c["id"])))
    assert inv["number"].startswith("PS/")
    assert db.scalar(select(LedgerEntry.ref_number).where(LedgerEntry.ref_id == inv["id"])) == inv["number"]
    assert db.get(Invoice, inv["id"]).pdf_name == inv["number"].replace("/", "-") + ".pdf"
