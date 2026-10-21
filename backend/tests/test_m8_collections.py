"""M8 acceptance criteria: link payments, duplicate UTRs, one 'due today' reminder; plus allocation and cheques."""

import hashlib
import hmac
import json
from datetime import timedelta
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import select

from app.models import Direction, Invoice, Message, MessageTemplate, Payment, ReminderLog, Role, Tenant
from app.models import TemplateCategory, TemplateStatus
from app.services import gateways
from app.services.invoices import today
from app.services.reminders import run_reminders
from tests.test_m3_catalog import customer
from tests.test_m4_whatsapp import connect_simulator, sim_text
from tests.test_m7_invoices import approved_order, biz, inv_of, ledger, make_invoice  # noqa: F401

API = "/api/v1"
NUM = "9876500001"


@pytest.fixture
def shop(biz):  # noqa: F811
    """Seller with GSTIN, WhatsApp simulator, one buyer in an open chat, two invoices (1770 then 885)."""
    connect_simulator(biz.client, biz.h)
    biz.c = customer(biz.client, biz.h, contact_person="Murugan", credit_days=30, whatsapp_opt_in=True)
    sim_text(biz.client, biz.h, NUM, "hi")
    biz.inv1 = inv_of(biz, make_invoice(biz, approved_order(biz, biz.c["id"], qty=10)))
    biz.inv2 = inv_of(biz, make_invoice(biz, approved_order(biz, biz.c["id"], qty=5)))
    return biz


def pay(b, expect=201, **body):
    r = b.client.post(f"{API}/payments", headers=body.pop("headers", b.h), json={"customer_id": b.c["id"], **body})
    assert r.status_code == expect, r.text
    return r.json()


def invoice(b, inv_id):
    return b.client.get(f"{API}/invoices/{inv_id}", headers=b.h).json()


def last_out(db, customer_id) -> Message:
    db.expire_all()
    return db.scalars(select(Message).where(Message.customer_id == customer_id, Message.direction == Direction.outbound)
                      .order_by(Message.created_at.desc())).first()


def test_cash_payment_allocates_oldest_first_and_sends_receipt(shop, db):
    p = pay(shop, amount=2000, mode="cash")
    assert p["status"] == "confirmed" and p["number"].startswith("RCPT/")
    assert [(a["invoice_number"], a["amount"]) for a in p["allocations"]] == [
        (shop.inv1["number"], "1770.00"), (shop.inv2["number"], "230.00")]
    assert invoice(shop, shop.inv1["id"])["balance_due"] == "0.00"
    assert invoice(shop, shop.inv2["id"])["balance_due"] == "655.00"
    st = ledger(shop, shop.c["id"])
    assert st["entries"][-1]["kind"] == "payment" and st["closing"] == "655.00"
    receipt = last_out(db, shop.c["id"])
    assert p["receipt_sent"] and "₹2,000.00" in receipt.text and "Balance due now: ₹655.00" in receipt.text


def test_overpayment_kept_as_advance(shop):
    p = pay(shop, amount=3000, mode="neft", reference="SBIN0001112223")
    assert p["unallocated"] == "345.00"
    assert ledger(shop, shop.c["id"])["closing"] == "-345.00"


def test_link_for_specific_invoice_and_receipt(shop, db):
    """AC: a payment through a link marks the right invoices paid and sends a receipt."""
    r = shop.client.post(f"{API}/payment-links", headers=shop.h,
                         json={"customer_id": shop.c["id"], "invoice_ids": [shop.inv2["id"]]})
    assert r.status_code == 201, r.text
    link = r.json()
    assert link["amount"] == "885.00" and link["short_url"].endswith(f"/pay/{link['id']}")
    assert link["short_url"] in last_out(db, shop.c["id"]).text  # sent on WhatsApp
    # buyer pays on the (simulated) gateway page
    assert shop.client.get(f"{API}/public/pay/{link['id']}").json()["amount"] == "885.00"
    assert shop.client.post(f"{API}/public/pay/{link['id']}").status_code == 200
    assert invoice(shop, shop.inv2["id"])["balance_due"] == "0.00"
    assert invoice(shop, shop.inv1["id"])["balance_due"] == "1770.00"
    assert "Payment received" in last_out(db, shop.c["id"]).text
    # paying again is refused; still one payment
    assert shop.client.post(f"{API}/payment-links/{link['id']}/simulate-paid", headers=shop.h).status_code == 409
    assert len(db.scalars(select(Payment).where(Payment.link_id == link["id"])).all()) == 1


def test_duplicate_utr_never_recorded_twice(shop, db):
    """AC: the same UTR twice gives one payment and one duplicate warning."""
    pay(shop, amount=1770, mode="upi", reference="412345678901")
    r = shop.client.post(f"{API}/payments", headers=shop.h,
                         json={"customer_id": shop.c["id"], "amount": 1770, "mode": "upi", "reference": "4123 4567 8901"})
    assert r.status_code == 409 and "already recorded" in r.json()["detail"]
    # the buyer sends the same proof on WhatsApp
    sim_text(shop.client, shop.h, NUM, "paid 1770 by gpay UTR 412345678901")
    assert "already recorded" in last_out(db, shop.c["id"]).text
    db.expire_all()
    assert len(db.scalars(select(Payment).where(Payment.reference == "412345678901")).all()) == 1


def test_buyer_proof_becomes_proposal_until_accounts_confirm(shop, db):
    sim_text(shop.client, shop.h, NUM, "paid Rs. 1,770 via phonepe utr 555566667777")
    assert "accounts team will confirm" in last_out(db, shop.c["id"]).text
    props = shop.client.get(f"{API}/payments", headers=shop.h, params={"status": "proposed"}).json()["items"]
    assert len(props) == 1 and props[0]["amount"] == "1770.00" and props[0]["reference"] == "555566667777"
    assert ledger(shop, shop.c["id"])["entries"][-1]["kind"] == "invoice"  # nothing posted yet
    # second identical proof -> duplicate warning, not a second proposal
    sim_text(shop.client, shop.h, NUM, "paid Rs. 1,770 via phonepe utr 555566667777")
    assert "already recorded" in last_out(db, shop.c["id"]).text
    pid = props[0]["id"]
    body = {"customer_id": shop.c["id"], "amount": "1770", "mode": "upi", "payment_date": today().isoformat(),
            "reference": "555566667777"}
    sales = shop.company.h(Role.sales)
    assert shop.client.post(f"{API}/payments/{pid}/confirm", headers=sales, json=body).status_code == 403
    r = shop.client.post(f"{API}/payments/{pid}/confirm", headers=shop.company.h(Role.accounts), json=body)
    assert r.status_code == 200 and r.json()["status"] == "confirmed"
    assert invoice(shop, shop.inv1["id"])["balance_due"] == "0.00"
    # a second proposal can be rejected
    sim_text(shop.client, shop.h, NUM, "sent 500 utr 999988887777")
    p2 = shop.client.get(f"{API}/payments", headers=shop.h, params={"status": "proposed"}).json()["items"][0]
    r = shop.client.post(f"{API}/payments/{p2['id']}/reject", headers=shop.h, json={"reason": "Not in bank statement"})
    assert r.json()["status"] == "rejected"


def test_cheque_clear_and_bounce(shop, db):
    p = pay(shop, amount=1770, mode="cheque", reference="123456", cheque_bank="SBI")
    assert p["status"] == "pending_clearance" and p["allocations"] == []
    assert ledger(shop, shop.c["id"])["closing"] == "2655.00"  # not counted yet
    p = shop.client.post(f"{API}/payments/{p['id']}/clear", headers=shop.h).json()
    assert p["status"] == "confirmed" and ledger(shop, shop.c["id"])["closing"] == "885.00"
    p = shop.client.post(f"{API}/payments/{p['id']}/bounce", headers=shop.h, json={"reason": "Insufficient funds"}).json()
    assert p["status"] == "bounced" and p["allocations"] == []
    st = ledger(shop, shop.c["id"])
    assert st["entries"][-1]["kind"] == "payment_reversed" and st["closing"] == "2655.00"
    assert invoice(shop, shop.inv1["id"])["balance_due"] == "1770.00"
    # a bounced cheque number can be presented again
    pay(shop, amount=1770, mode="cheque", reference="123456")


def _due_today(db, *invoice_ids, days=0):
    for i in invoice_ids:
        db.get(Invoice, i).due_date = today() - timedelta(days=days)
    db.commit()


def test_due_today_reminder_sent_exactly_once(shop, db):
    """AC: an invoice due today receives exactly one 'due today' reminder."""
    _due_today(db, shop.inv1["id"])
    tenant = db.get(Tenant, shop.company.tenant_id)
    r1 = run_reminders(db, tenant)
    from app.services import orders as order_svc
    order_svc.commit_and_send(db)
    r2 = run_reminders(db, tenant)
    order_svc.commit_and_send(db)
    assert r1["sent"] == 1 and r2 == {"customers": 0, "sent": 0, "skipped": 0}
    logs = db.scalars(select(ReminderLog).where(ReminderLog.invoice_id == shop.inv1["id"])).all()
    assert len(logs) == 1 and logs[0].offset_days == 0 and logs[0].message_id
    msg = db.get(Message, logs[0].message_id)
    assert "due today" in msg.text and "Total due: ₹2,655.00" in msg.text and "/pay/" in msg.text
    # via the API it runs today's step only, and never repeats
    assert shop.client.post(f"{API}/collections/reminders/run", headers=shop.h).json()["sent"] == 0
    log_api = shop.client.get(f"{API}/collections/reminders", headers=shop.h).json()
    assert log_api["items"][0]["step"] == "due today" and log_api["items"][0]["sent"]


def test_reminder_steps_pauses_and_opt_out(shop, db):
    tenant = db.get(Tenant, shop.company.tenant_id)
    _due_today(db, shop.inv1["id"], days=-3)   # due in 3 days
    _due_today(db, shop.inv2["id"], days=5)    # 5 days overdue: not a step
    assert run_reminders(db, tenant)["sent"] == 1
    db.commit()
    assert db.scalars(select(ReminderLog)).one().offset_days == -3
    # paused invoice / customer are skipped
    _due_today(db, shop.inv2["id"], days=7)
    shop.client.put(f"{API}/invoices/{shop.inv2['id']}/reminders", headers=shop.h, json={"paused": True})
    db.expire_all()
    assert run_reminders(db, db.get(Tenant, tenant.id))["customers"] == 0
    shop.client.put(f"{API}/invoices/{shop.inv2['id']}/reminders", headers=shop.h, json={"paused": False})
    shop.client.put(f"{API}/customers/{shop.c['id']}/reminders", headers=shop.h, json={"paused": True})
    db.expire_all()
    r = run_reminders(db, db.get(Tenant, tenant.id))
    db.commit()
    assert r["skipped"] == 1
    assert "paused" in db.scalars(select(ReminderLog).where(ReminderLog.invoice_id == shop.inv2["id"])).one().note


def test_reminder_outside_window_uses_template(biz, db):  # noqa: F811
    connect_simulator(biz.client, biz.h)
    c = customer(biz.client, biz.h, contact_person="Ravi", whatsapp_opt_in=True)
    inv = inv_of(biz, make_invoice(biz, approved_order(biz, c["id"])))
    db.add(MessageTemplate(tenant_id=biz.company.tenant_id, name="payment_reminder", language="en",
                           category=TemplateCategory.utility, body="Hi {{1}}, ₹{{2}} is due. Pay: {{3}}",
                           variable_count=3, status=TemplateStatus.approved))
    db.get(Invoice, inv["id"]).due_date = today()
    db.commit()
    assert run_reminders(db, db.get(Tenant, biz.company.tenant_id))["sent"] == 1
    db.commit()
    m = db.scalars(select(Message).where(Message.customer_id == c["id"], Message.direction == Direction.outbound)).one()
    assert m.msg_type.value == "template" and "Ravi" in m.text and "1,770.00" in m.text and "/pay/" in m.text


def test_razorpay_link_and_webhook(shop, db, monkeypatch):
    calls = []

    def handler(req: httpx.Request):
        calls.append(req)
        if req.url.path.endswith("/payment_links"):
            return httpx.Response(200, json={"id": "plink_ABC123", "short_url": "https://rzp.io/i/abc", "status": "created"})
        return httpx.Response(404, json={})
    monkeypatch.setattr(gateways, "TRANSPORT", httpx.MockTransport(handler))
    h = shop.h
    r = shop.client.put(f"{API}/payments/gateway", headers=h, json={
        "mode": "razorpay", "key_id": "rzp_test_123", "key_secret": "sekret", "webhook_secret": "whsec"})
    assert r.status_code == 200 and r.json()["key_secret_masked"] != "sekret"
    link = shop.client.post(f"{API}/payment-links", headers=h, json={"customer_id": shop.c["id"], "send": False}).json()
    body = json.loads(calls[-1].content)
    assert link["short_url"] == "https://rzp.io/i/abc" and body["amount"] == 265500 and body["currency"] == "INR"
    assert calls[-1].headers["authorization"].startswith("Basic ")
    event = {"event": "payment_link.paid", "payload": {
        "payment_link": {"entity": {"id": "plink_ABC123", "status": "paid"}},
        "payment": {"entity": {"id": "pay_XYZ", "amount": 265500, "method": "upi"}}}}
    raw = json.dumps(event).encode()
    url = f"/api/webhooks/razorpay/{shop.company.tenant_id}"
    assert shop.client.post(url, content=raw, headers={"X-Razorpay-Signature": "bad"}).status_code == 401
    sig = hmac.new(b"whsec", raw, hashlib.sha256).hexdigest()
    for _ in range(2):  # gateways retry webhooks; must stay one payment
        assert shop.client.post(url, content=raw, headers={"X-Razorpay-Signature": sig}).status_code == 200
    db.expire_all()
    pays = db.scalars(select(Payment).where(Payment.gateway_payment_id == "pay_XYZ")).all()
    assert len(pays) == 1 and pays[0].amount == Decimal("2655.00")
    assert invoice(shop, shop.inv1["id"])["balance_due"] == "0.00" and invoice(shop, shop.inv2["id"])["balance_due"] == "0.00"


def test_ageing_and_dashboard(shop, db):
    _due_today(db, shop.inv1["id"], days=45)
    _due_today(db, shop.inv2["id"], days=-5)
    pay(shop, amount=500, mode="cash")
    rows = shop.client.get(f"{API}/collections/ageing", headers=shop.h).json()
    r = rows[0]
    assert r["d31_60"] == "1270.00" and r["not_due"] == "885.00" and r["overdue"] == "1270.00" and r["total"] == "2155.00"
    assert shop.client.get(f"{API}/collections/ageing/export", headers=shop.h).status_code == 200
    d = shop.client.get(f"{API}/collections/dashboard", headers=shop.h).json()
    assert d["total_outstanding"] == "2155.00" and d["overdue"] == "1270.00"
    assert d["collected_this_month"] == "500.00" and d["collected_offline"] == "500.00" and d["collected_via_link"] == "0.00"


def test_sales_permissions(shop):
    sales_user = shop.company.users[Role.sales]
    mine = customer(shop.client, shop.h, number="9876500005", assigned_user_id=str(sales_user.id))
    sales = shop.company.h(Role.sales)
    r = shop.client.post(f"{API}/payments", headers=sales, json={"customer_id": mine["id"], "amount": 100, "mode": "cash"})
    assert r.status_code == 201
    r = shop.client.post(f"{API}/payments", headers=sales, json={"customer_id": mine["id"], "amount": 100, "mode": "upi",
                                                                 "reference": "123412341234"})
    assert r.status_code == 403
    r = shop.client.post(f"{API}/payments", headers=sales, json={"customer_id": shop.c["id"], "amount": 1, "mode": "cash"})
    assert r.status_code == 404  # not their customer
    assert shop.client.get(f"{API}/payments", headers=sales).json()["total"] == 1
    admin = shop.company.h(Role.admin)
    assert shop.client.post(f"{API}/payments", headers=admin, json={"customer_id": mine["id"], "amount": 1,
                                                                    "mode": "cash"}).status_code == 403  # Admin: view


def test_credit_note_settles_invoice(shop):
    r = shop.client.post(f"{API}/credit-notes", headers=shop.h, json={
        "customer_id": shop.c["id"], "invoice_id": shop.inv2["id"], "amount": 885, "reason": "Returned"})
    assert r.status_code == 201
    assert invoice(shop, shop.inv2["id"])["balance_due"] == "0.00"
    p = pay(shop, amount=2000, mode="cash")
    assert [a["invoice_number"] for a in p["allocations"]] == [shop.inv1["number"]] and p["unallocated"] == "230.00"
