"""M9 acceptance criteria: 200 buyers with 10 opted out -> exactly 190 sent; "orders placed" matches orders from
recipients within 7 days. Plus segments, limits, test send, media header, "Order now", schedule and cancel."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import select, update

from app.models import (
    Campaign,
    CampaignRecipient,
    Customer,
    Message,
    MessageStatus,
    Order,
    RecipientStatus,
    Role,
    Tenant,
)
from app.services import campaigns as svc
from app.services import orders as order_svc
from tests.test_m3_catalog import customer, product
from tests.test_m4_whatsapp import connect_simulator

API = "/api/v1"


def template(client, h, name="diwali_offer", category="MARKETING", body="Hi {{1}}, {{2}}", **extra):
    r = client.post(f"{API}/whatsapp/templates", headers=h, json={
        "name": name, "category": category, "body": body,
        "buttons": extra.pop("buttons", [{"type": "QUICK_REPLY", "text": "Order now"},
                                         {"type": "QUICK_REPLY", "text": "Not now"}]), **extra})
    assert r.status_code == 201, r.text
    t = r.json()
    r = client.post(f"{API}/simulator/templates/{t['id']}/approve", headers=h)
    assert r.status_code == 200, r.text
    return r.json()


@pytest.fixture
def mkt(client, company_a):
    h = company_a.h(Role.owner)
    connect_simulator(client, h)
    tpl = template(client, h)
    return SimpleNamespace(client=client, h=h, company=company_a, tpl=tpl)


def bulk_customers(db, tenant_id, n, *, start=0, opted_out=0, no_opt_in=0, **fields):
    """Fast insert of n active customers: the first `opted_out` opted out, the next `no_opt_in` without opt-in."""
    for i in range(n):
        out = i < opted_out
        no = opted_out <= i < opted_out + no_opt_in
        db.add(Customer(tenant_id=tenant_id, business_name=f"Buyer {start + i:04d}",
                        contact_person=f"Ravi{start + i} Kumar", whatsapp_number=f"+9190000{start + i:05d}",
                        tags=fields.get("tags", []), city=fields.get("city"), state=fields.get("state"),
                        whatsapp_opt_in=not (out or no), opted_out=out, opt_in_at=datetime.now(UTC)))
    db.commit()


def create(b, expect=201, **body):
    payload = {"name": "Diwali offer", "template_id": b.tpl["id"],
               "variables": [{"kind": "field", "value": "first_name"}, {"kind": "fixed", "value": "10% off this week"}],
               "segment": {}, "order_button_index": 0, **body}
    r = b.client.post(f"{API}/campaigns", headers=body.pop("headers", b.h), json=payload)
    assert r.status_code == expect, r.text
    return r.json()


def preview(b, segment=None):
    r = b.client.post(f"{API}/campaigns/preview", headers=b.h, json=segment or {})
    assert r.status_code == 200, r.text
    return r.json()


def send(b, cid, count, expect=200, headers=None):
    r = b.client.post(f"{API}/campaigns/{cid}/send", headers=headers or b.h, json={"confirmed_count": count})
    assert r.status_code == expect, r.text
    return r.json()


def get(b, cid):
    return b.client.get(f"{API}/campaigns/{cid}", headers=b.h).json()


def test_200_segment_with_10_opted_out_sends_exactly_190(mkt, db):
    """AC: a campaign to a 200-customer segment with 10 opted-out customers sends to exactly 190."""
    bulk_customers(db, mkt.company.tenant_id, 200, opted_out=10, tags=["retail"])
    p = preview(mkt, {"tags": ["retail"]})
    assert (p["matched"], p["eligible"], p["opted_out"], p["no_opt_in"]) == (200, 190, 10, 0)
    assert p["estimated_cost"] == "167.20"  # 190 x Rs 0.88
    c = create(mkt, segment={"tags": ["retail"]})
    out = send(mkt, c["id"], 190)
    assert out["status"] == "completed"
    st = out["stats"]
    assert st["targeted"] == 200 and st["excluded"] == 10 and st["sent"] == 190 and st["failed"] == 0
    assert st["excluded_reasons"] == [{"reason": "Opted out of WhatsApp messages", "count": 10}]
    msgs = db.scalars(select(Message).where(Message.tenant_id == mkt.company.tenant_id)).all()
    assert len(msgs) == 190
    assert {m.customer_id for m in msgs}.isdisjoint(
        set(db.scalars(select(Customer.id).where(Customer.opted_out.is_(True)))))
    # variables filled per buyer, Order-now payload carries the campaign id
    m = db.scalar(select(Message).join(Customer, Customer.id == Message.customer_id)
                  .where(Customer.business_name == "Buyer 0010"))
    assert m.text == "Hi Ravi10, 10% off this week"
    comps = m.payload["template"]["components"]
    assert comps[-1] == {"type": "button", "sub_type": "quick_reply", "index": "0",
                         "parameters": [{"type": "payload", "payload": f"campaign:order:{c['id']}"}]}


def test_segment_filters_and_no_opt_in_excluded(mkt, db):
    tid = mkt.company.tenant_id
    bulk_customers(db, tid, 3, city="Coimbatore", tags=["dealer"])
    bulk_customers(db, tid, 2, start=10, city="Madurai", no_opt_in=1)
    assert preview(mkt, {"cities": ["coimbatore"]})["eligible"] == 3
    p = preview(mkt, {"cities": ["Madurai"]})
    assert (p["matched"], p["eligible"], p["no_opt_in"]) == (2, 1, 1)
    assert preview(mkt, {"tags": ["dealer", "other"]})["matched"] == 3
    # outstanding: one Madurai buyer owes 5000 (opening balance)
    db.execute(update(Customer).where(Customer.business_name == "Buyer 0011").values(opening_balance=5000))
    db.commit()
    assert preview(mkt, {"min_outstanding": "1000"})["matched"] == 1
    assert preview(mkt, {"max_outstanding": "0"})["matched"] == 4
    # last order date: nobody has ordered -> all match "no order in 30 days", none "ordered within 30 days"
    assert preview(mkt, {"no_order_days": 30})["matched"] == 5
    assert preview(mkt, {"ordered_within_days": 30})["matched"] == 0


def test_only_owner_and_admin_can_send(mkt, db):
    bulk_customers(db, mkt.company.tenant_id, 2)
    c = create(mkt)
    sales = mkt.company.h(Role.sales)
    assert mkt.client.post(f"{API}/campaigns/{c['id']}/send", headers=sales, json={"confirmed_count": 2}).status_code == 403
    assert mkt.client.get(f"{API}/campaigns", headers=mkt.company.h(Role.accounts)).status_code == 403
    assert send(mkt, c["id"], 2, headers=mkt.company.h(Role.admin))["status"] == "completed"


def test_confirmed_count_must_match_and_template_rules(mkt, db):
    bulk_customers(db, mkt.company.tenant_id, 3)
    c = create(mkt)
    r = mkt.client.post(f"{API}/campaigns/{c['id']}/send", headers=mkt.h, json={"confirmed_count": 5})
    assert r.status_code == 409 and "now has 3 buyers" in r.json()["detail"]
    utility = template(mkt.client, mkt.h, name="order_note", category="UTILITY", body="Order {{1}}")
    create(mkt, expect=400, template_id=utility["id"])
    # missing variable value
    c2 = create(mkt, variables=[{"kind": "field", "value": "first_name"}, {"kind": "fixed", "value": ""}])
    r = mkt.client.post(f"{API}/campaigns/{c2['id']}/send", headers=mkt.h, json={"confirmed_count": 3})
    assert r.status_code == 400 and "is empty" in r.json()["detail"]


def test_messaging_limit_throttles_and_rest_sends_later(mkt, db):
    """FR-M9-04: never more than the number's 24-hour limit; the remainder goes when the limit allows."""
    bulk_customers(db, mkt.company.tenant_id, 8)
    mkt.client.patch(f"{API}/company", headers=mkt.h, json={"wa_daily_limit": 5})
    c = create(mkt)
    out = send(mkt, c["id"], 8)
    assert out["status"] == "sending" and out["stats"]["sent"] == 5 and out["stats"]["waiting"] == 3
    assert "3 message(s) waiting" in out["waiting_note"]
    # nothing more goes while the limit is used up
    assert svc.run_due(db, mkt.company.tenant_id) == 1
    db.commit()
    assert db.scalar(select(Campaign).where(Campaign.id == c["id"])).status.value == "sending"
    # 24 hours later the earlier messages no longer count
    db.execute(update(Message).values(created_at=datetime.now(UTC) - timedelta(hours=25)))
    db.commit()
    out = get(mkt, c["id"])  # screens catch up without a beat worker in development
    assert out["status"] == "completed" and out["stats"]["sent"] == 8 and out["waiting_note"] is None


def test_test_send_to_one_number(mkt, db):
    bulk_customers(db, mkt.company.tenant_id, 3)
    me = customer(mkt.client, mkt.h, number="9800012345", business_name="Owner test", contact_person="Selvam",
                  whatsapp_opt_in=True, tags=["test"])
    c = create(mkt)
    r = mkt.client.post(f"{API}/campaigns/{c['id']}/test", headers=mkt.h, json={"whatsapp_number": "98000 12345"})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "sent"
    msgs = db.scalars(select(Message).where(Message.customer_id == me["id"])).all()
    assert len(msgs) == 1 and msgs[0].text == "Hi Selvam, 10% off this week"
    after = get(mkt, c["id"])
    assert after["status"] == "draft" and after["last_test_to"] == "+919800012345"
    r = mkt.client.post(f"{API}/campaigns/{c['id']}/test", headers=mkt.h, json={"whatsapp_number": "9111122222"})
    assert r.status_code == 400 and "Add this number as a customer" in r.json()["detail"]


def test_image_header_needs_attachment_and_goes_in_header(mkt, db):
    tpl = template(mkt.client, mkt.h, name="new_launch", header_format="IMAGE", body="New: {{1}}",
                   buttons=[{"type": "QUICK_REPLY", "text": "Order now"}])
    assert tpl["header_format"] == "IMAGE"
    bulk_customers(db, mkt.company.tenant_id, 2)
    c = create(mkt, template_id=tpl["id"], variables=[{"kind": "fixed", "value": "Gold Atta 5kg"}])
    r = mkt.client.post(f"{API}/campaigns/{c['id']}/send", headers=mkt.h, json={"confirmed_count": 2})
    assert r.status_code == 400 and "Attach the image" in r.json()["detail"]
    bad = mkt.client.post(f"{API}/campaigns/{c['id']}/media", headers=mkt.h,
                          files={"file": ("a.pdf", b"%PDF-1.4", "application/pdf")})
    assert bad.status_code == 400
    r = mkt.client.post(f"{API}/campaigns/{c['id']}/media", headers=mkt.h,
                        files={"file": ("launch.png", b"\x89PNG\r\n\x1a\nfake", "image/png")})
    assert r.status_code == 200 and r.json()["has_media"]
    assert mkt.client.get(f"{API}/campaigns/{c['id']}/media", headers=mkt.h).content.startswith(b"\x89PNG")
    send(mkt, c["id"], 2)
    msgs = db.scalars(select(Message).where(Message.tenant_id == mkt.company.tenant_id)).all()
    media_ids = {m.payload["template"]["components"][0]["parameters"][0]["image"]["id"] for m in msgs}
    assert len(media_ids) == 1  # uploaded once, reused
    assert all(m.media_mime == "image/png" for m in msgs)


def test_order_now_tap_suggests_products_and_report_counts_orders(mkt, db):
    """FR-M9-07 + AC: "orders placed" matches orders from recipients within 7 days of the send."""
    tid = mkt.company.tenant_id
    atta = product(mkt.client, mkt.h, "ATTA-5", 250, name="Gold Atta 5kg", uom="BAG")
    bulk_customers(db, tid, 3, tags=["retail"])
    outsider = customer(mkt.client, mkt.h, number="9811111111", whatsapp_opt_in=True)
    c = create(mkt, segment={"tags": ["retail"]}, product_ids=[atta["id"]])
    send(mkt, c["id"], 3)
    buyer = db.scalar(select(Customer).where(Customer.business_name == "Buyer 0000"))
    # buyer taps "Order now" on the template (simulator sends the button payload)
    r = mkt.client.post(f"{API}/simulator/inbound", headers=mkt.h, json={
        "from_number": buyer.whatsapp_number, "reply_id": f"campaign:order:{c['id']}", "reply_title": "Order now"})
    assert r.status_code == 200, r.text
    reply = db.scalars(select(Message).where(Message.customer_id == buyer.id)
                       .order_by(Message.created_at.desc())).first()
    assert "Gold Atta 5kg: ₹250.00 per BAG" in reply.text and "Reply with the items" in reply.text
    # orders: buyer orders twice (one confirmed, one still a quote), outsider orders too
    for cust_id, action in ((buyer.id, "confirm"), (buyer.id, "quote"), (outsider["id"], "confirm")):
        r = mkt.client.post(f"{API}/orders", headers=mkt.h, json={
            "customer_id": str(cust_id), "action": action, "lines": [{"product_id": atta["id"], "quantity": 4}]})
        assert r.status_code == 201, r.text
    st = get(mkt, c["id"])["stats"]
    assert st["order_taps"] == 1 and st["replied"] == 1
    assert st["orders"] == 1 and st["order_value"] == "1050.00" and st["ordering_buyers"] == 1
    # an order placed 8 days after the message does not count
    o = db.scalars(select(Order).where(Order.customer_id == buyer.id, Order.status == "confirmed")).first()
    o.created_at = datetime.now(UTC) + timedelta(days=8)
    db.commit()
    assert get(mkt, c["id"])["stats"]["orders"] == 0
    rows = mkt.client.get(f"{API}/campaigns/{c['id']}/recipients", headers=mkt.h, params={"view": "tapped"}).json()
    assert rows["total"] == 1 and rows["items"][0]["order_tapped"] and rows["items"][0]["replied"]


def test_meta_failure_shows_reason_and_is_not_retried(mkt, db):
    bulk_customers(db, mkt.company.tenant_id, 2)
    c = create(mkt)
    send(mkt, c["id"], 2)
    m = db.scalars(select(Message).where(Message.tenant_id == mkt.company.tenant_id)).first()
    r = mkt.client.post(f"{API}/simulator/status", headers=mkt.h, json={
        "message_id": str(m.id), "status": "failed",
        "error": "Message failed to send to maintain healthy ecosystem engagement"})
    assert r.status_code == 200
    st = get(mkt, c["id"])["stats"]
    assert st["failed"] == 1 and st["sent"] == 1
    assert "healthy ecosystem" in st["failed_reasons"][0]["reason"]
    db.expire_all()
    assert db.get(Message, m.id).status == MessageStatus.failed
    rows = mkt.client.get(f"{API}/campaigns/{c['id']}/recipients", headers=mkt.h, params={"view": "failed"}).json()
    assert rows["total"] == 1 and rows["items"][0]["delivery"] == "failed"


def test_schedule_runs_at_time_and_cancel(mkt, db):
    tid = mkt.company.tenant_id
    bulk_customers(db, tid, 3)
    c = create(mkt)
    past = mkt.client.post(f"{API}/campaigns/{c['id']}/schedule", headers=mkt.h,
                           json={"scheduled_at": "2020-01-01T10:00:00+05:30"})
    assert past.status_code == 400
    when = (datetime.now(UTC) + timedelta(hours=2)).isoformat()
    r = mkt.client.post(f"{API}/campaigns/{c['id']}/schedule", headers=mkt.h, json={"scheduled_at": when})
    assert r.status_code == 200 and r.json()["status"] == "scheduled"
    assert mkt.client.put(f"{API}/campaigns/{c['id']}", headers=mkt.h, json={"name": "x" * 5}).status_code == 409
    assert svc.run_due(db, tid) == 0
    # time arrives
    db.execute(update(Campaign).values(scheduled_at=datetime.now(UTC) - timedelta(minutes=1)))
    db.commit()
    assert svc.run_due(db, tid) == 1
    order_svc.commit_and_send(db)
    assert get(mkt, c["id"])["stats"]["sent"] == 3

    # cancel while waiting on the limit: pending buyers are skipped
    bulk_customers(db, tid, 4, start=100)
    mkt.client.patch(f"{API}/company", headers=mkt.h, json={"wa_daily_limit": 5})
    c2 = create(mkt, name="Second")
    out = send(mkt, c2["id"], 7)
    assert out["stats"]["sent"] == 2 and out["status"] == "sending"
    out = mkt.client.post(f"{API}/campaigns/{c2['id']}/cancel", headers=mkt.h).json()
    assert out["status"] == "cancelled" and out["stats"]["waiting"] == 0
    skipped = db.scalars(select(CampaignRecipient).where(CampaignRecipient.campaign_id == c2["id"],
                                                         CampaignRecipient.status == RecipientStatus.skipped)).all()
    assert len(skipped) == 5


def test_list_duplicate_and_delete(mkt, db):
    bulk_customers(db, mkt.company.tenant_id, 2)
    c = create(mkt)
    send(mkt, c["id"], 2)
    page = mkt.client.get(f"{API}/campaigns", headers=mkt.h).json()
    assert page["total"] == 1 and page["items"][0]["sent"] == 2
    dup = mkt.client.post(f"{API}/campaigns/{c['id']}/duplicate", headers=mkt.h).json()
    assert dup["status"] == "draft" and dup["name"] == "Diwali offer (copy)"
    assert mkt.client.delete(f"{API}/campaigns/{c['id']}", headers=mkt.h).status_code == 409
    assert mkt.client.delete(f"{API}/campaigns/{dup['id']}", headers=mkt.h).status_code == 204
    t = db.get(Tenant, mkt.company.tenant_id)
    assert t.wa_daily_limit == 1000
    opts = mkt.client.get(f"{API}/campaigns/options", headers=mkt.h).json()
    assert [x["name"] for x in opts["templates"]] == ["diwali_offer"]
