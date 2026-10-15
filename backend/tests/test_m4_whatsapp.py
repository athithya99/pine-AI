"""M4 acceptance criteria and WhatsApp rules."""

import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import func, select

from app.models import Conversation, Customer, Message, Role
from app.services.whatsapp import client as wa_client

API = "/api/v1"
SECRET = "test-app-secret"


# ---------------- helpers ----------------
def connect_simulator(client, h):
    r = client.put(f"{API}/whatsapp/account", headers=h, json={"mode": "simulator"})
    assert r.status_code == 200, r.text
    return r.json()


def connect_cloud(client, h, pnid="1000000001", token="EAAtest", secret=SECRET):
    r = client.put(f"{API}/whatsapp/account", headers=h, json={
        "mode": "cloud", "waba_id": "2000000001", "phone_number_id": pnid, "display_phone_number": "+91 90000 00000",
        "access_token": token, "app_secret": secret})
    assert r.status_code == 200, r.text
    return r.json()


def add_customer(client, h, number="9876500001", **extra):
    r = client.post(f"{API}/customers", headers=h, json={"business_name": "Murugan Stores", "whatsapp_number": number, **extra})
    assert r.status_code == 201, r.text
    return r.json()


def sim_text(client, h, number, text, name=None):
    r = client.post(f"{API}/simulator/inbound", headers=h, json={"from_number": number, "text": text, "profile_name": name})
    assert r.status_code == 200, r.text
    return r.json()


def conversations(client, h):
    return client.get(f"{API}/inbox/conversations", headers=h).json()["items"]


def messages(client, h, conv_id):
    return client.get(f"{API}/inbox/conversations/{conv_id}/messages", headers=h).json()


def wa_payload(pnid, messages_=None, statuses=None, from_="919876500001", name="Murugan"):
    value = {"messaging_product": "whatsapp", "metadata": {"display_phone_number": "919000000000", "phone_number_id": pnid}}
    if messages_:
        value["contacts"] = [{"profile": {"name": name}, "wa_id": from_}]
        value["messages"] = messages_
    if statuses:
        value["statuses"] = statuses
    return {"object": "whatsapp_business_account", "entry": [{"id": "2000000001", "changes": [{"field": "messages", "value": value}]}]}


def post_webhook(client, payload, secret=SECRET):
    raw = json.dumps(payload).encode()
    headers = {"Content-Type": "application/json"}
    if secret is not None:
        headers["X-Hub-Signature-256"] = "sha256=" + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    return client.post("/api/webhooks/whatsapp", content=raw, headers=headers)


def ts(dt=None):
    return str(int((dt or datetime.now(UTC)).timestamp()))


class FakeMeta:
    """Stands in for graph.facebook.com."""

    def __init__(self):
        self.sent: list[dict] = []
        self.fail_with: tuple[int, dict] | None = None
        self.calls = 0
        self.subscribed = False

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        path = request.url.path
        if path.endswith("/messages"):
            if self.fail_with:
                return httpx.Response(self.fail_with[0], json=self.fail_with[1])
            body = json.loads(request.content)
            self.sent.append(body)
            if body.get("status") == "read":
                return httpx.Response(200, json={"success": True})
            return httpx.Response(200, json={"messages": [{"id": f"wamid.OUT{len(self.sent)}"}]})
        if path.endswith("/message_templates"):
            if request.method == "POST":
                return httpx.Response(200, json={"id": "tpl123", "status": "PENDING", "category": "UTILITY"})
            return httpx.Response(200, json={"data": [{"id": "tpl123", "name": "payment_reminder", "language": "en",
                                                       "status": "APPROVED"}]})
        if path.endswith("/subscribed_apps"):
            self.subscribed = True
            if request.method == "POST":
                return httpx.Response(200, json={"success": True})
            return httpx.Response(200, json={"data": [{"whatsapp_business_api_data": {"name": "Pine AI Commerce"}}]})
        if path.endswith("/media"):
            return httpx.Response(200, json={"id": "media-up-1"})
        if "/media-" in path or path.endswith("/MEDIA1"):
            return httpx.Response(200, json={"url": "https://lookaside.example/file1", "mime_type": "image/jpeg"})
        if "lookaside" in str(request.url):
            return httpx.Response(200, content=b"\xff\xd8fakejpeg")
        return httpx.Response(404, json={"error": {"message": "not mocked", "code": 100}})


@pytest.fixture
def meta(monkeypatch):
    fake = FakeMeta()
    monkeypatch.setattr(wa_client, "TRANSPORT", httpx.MockTransport(fake.handler))
    return fake


# ---------------- tests ----------------
def test_webhook_verification(client):
    ok = client.get("/api/webhooks/whatsapp", params={"hub.mode": "subscribe", "hub.verify_token": "test-verify",
                                                       "hub.challenge": "12345"})
    assert ok.status_code == 200 and ok.text == "12345"
    bad = client.get("/api/webhooks/whatsapp", params={"hub.mode": "subscribe", "hub.verify_token": "nope",
                                                        "hub.challenge": "12345"})
    assert bad.status_code == 403


def test_unsigned_or_wrong_signature_rejected(client, company_a, meta):
    connect_cloud(client, company_a.h(Role.owner))
    payload = wa_payload("1000000001", [{"from": "919876500001", "id": "wamid.A", "timestamp": ts(), "type": "text",
                                         "text": {"body": "hi"}}])
    assert post_webhook(client, payload, secret=None).status_code == 401
    assert post_webhook(client, payload, secret="wrong").status_code == 401
    assert post_webhook(client, payload).status_code == 200


def test_inbound_text_photo_voice_reach_inbox(client, company_a, meta):
    """AC: a text, a photo and a voice note all appear in the inbox."""
    h = company_a.h(Role.owner)
    connect_cloud(client, h)
    add_customer(client, h)
    msgs = [
        {"from": "919876500001", "id": "wamid.T1", "timestamp": ts(), "type": "text", "text": {"body": "20 boxes salt"}},
        {"from": "919876500001", "id": "wamid.I1", "timestamp": ts(), "type": "image",
         "image": {"id": "MEDIA1", "mime_type": "image/jpeg", "caption": "my list"}},
        {"from": "919876500001", "id": "wamid.V1", "timestamp": ts(), "type": "audio",
         "audio": {"id": "MEDIA1", "mime_type": "audio/ogg", "voice": True}},
    ]
    assert post_webhook(client, wa_payload("1000000001", msgs)).status_code == 200
    convs = conversations(client, h)
    assert len(convs) == 1 and convs[0]["unread_count"] == 3 and convs[0]["customer_name"] == "Murugan Stores"
    thread = [m for m in messages(client, h, convs[0]["id"]) if m["direction"] == "inbound"]
    assert [m["msg_type"] for m in thread] == ["text", "image", "audio"]
    assert thread[1]["has_media"] and thread[1]["text"] == "my list"
    assert thread[2]["payload"]["voice"] is True
    # media was copied to our storage and is served to staff
    f = client.get(f"{API}/media/{thread[1]['id']}", headers=h)
    assert f.status_code == 200 and f.content == b"\xff\xd8fakejpeg"
    # opening the thread clears unread and sends a read receipt to Meta
    assert conversations(client, h)[0]["unread_count"] == 0
    assert any(b.get("status") == "read" for b in meta.sent)


def test_duplicate_webhook_processed_once(client, company_a, meta):
    """FR-M4-11: Meta may deliver the same message twice."""
    h = company_a.h(Role.owner)
    connect_cloud(client, h)
    payload = wa_payload("1000000001", [{"from": "919876500001", "id": "wamid.DUP", "timestamp": ts(), "type": "text",
                                         "text": {"body": "hello"}}])
    post_webhook(client, payload)
    post_webhook(client, payload)
    conv = conversations(client, h)[0]
    thread = messages(client, h, conv["id"])
    assert len([m for m in thread if m["direction"] == "inbound"]) == 1
    assert len([m for m in thread if m["direction"] == "outbound"]) == 1  # one bot greeting, not two


def test_unknown_number_becomes_lead(client, company_a):
    h = company_a.h(Role.owner)
    connect_simulator(client, h)
    sim_text(client, h, "9123456789", "price list pls", name="Senthil Traders")
    lead = client.get(f"{API}/customers?status=lead", headers=h).json()["items"]
    assert len(lead) == 1
    assert lead[0]["business_name"] == "Senthil Traders" and lead[0]["whatsapp_number"] == "+919123456789"
    assert lead[0]["whatsapp_opt_in"] is False


def test_routes_to_correct_tenant(client, company_a, company_b, meta):
    connect_cloud(client, company_a.h(Role.owner), pnid="1000000001")
    connect_cloud(client, company_b.h(Role.owner), pnid="1000000002")
    post_webhook(client, wa_payload("1000000002", [{"from": "919876500001", "id": "wamid.B1", "timestamp": ts(),
                                                    "type": "text", "text": {"body": "for B"}}]))
    assert conversations(client, company_a.h(Role.owner)) == []
    assert len(conversations(client, company_b.h(Role.owner))) == 1


def test_24_hour_window(client, company_a, db):
    """AC: free-form message 25 hours after the buyer's last message is blocked."""
    h = company_a.h(Role.owner)
    connect_simulator(client, h)
    c = add_customer(client, h)
    conv = client.post(f"{API}/inbox/customers/{c['id']}/conversation", headers=h).json()
    blocked = client.post(f"{API}/inbox/conversations/{conv['id']}/messages", headers=h, json={"text": "Hi"})
    assert blocked.status_code == 409 and blocked.json()["detail"]["code"] == "window_closed"

    sim_text(client, h, "9876500001", "hi")
    ok = client.post(f"{API}/inbox/conversations/{conv['id']}/messages", headers=h, json={"text": "Hello! How can we help?"})
    assert ok.status_code == 201 and ok.json()["status"] == "sent"

    row = db.get(Conversation, conv["id"])
    row.last_inbound_at = datetime.now(UTC) - timedelta(hours=25)
    db.commit()
    late = client.post(f"{API}/inbox/conversations/{conv['id']}/messages", headers=h, json={"text": "Still there?"})
    assert late.status_code == 409 and late.json()["detail"]["code"] == "window_closed"
    assert client.get(f"{API}/inbox/conversations/{conv['id']}", headers=h).json()["window_open"] is False


def test_templates_and_business_initiated_send(client, company_a):
    h = company_a.h(Role.owner)
    connect_simulator(client, h)
    opted = add_customer(client, h, "9876500001", whatsapp_opt_in=True)
    not_opted = add_customer(client, h, "9876500002")
    r = client.post(f"{API}/whatsapp/templates", headers=h, json={
        "name": "Payment Reminder", "category": "UTILITY",
        "body": "Hi {{1}}, invoice {{2}} is due. Please pay.", "example_values": ["Ravi", "INV-1"],
        "buttons": [{"text": "Pay now"}]})
    assert r.status_code == 201, r.text
    tpl = r.json()
    assert tpl["name"] == "payment_reminder" and tpl["status"] == "PENDING" and tpl["variable_count"] == 2

    conv = client.post(f"{API}/inbox/customers/{opted['id']}/conversation", headers=h).json()
    send = {"kind": "template", "template_id": tpl["id"], "template_params": ["Murugan", "INV-9"]}
    pending = client.post(f"{API}/inbox/conversations/{conv['id']}/messages", headers=h, json=send)
    assert pending.status_code == 409 and pending.json()["detail"]["code"] == "template_not_approved"

    client.post(f"{API}/simulator/templates/{tpl['id']}/approve", headers=h)
    sent = client.post(f"{API}/inbox/conversations/{conv['id']}/messages", headers=h, json=send)
    assert sent.status_code == 201, sent.text
    assert sent.json()["text"].startswith("Hi Murugan, invoice INV-9 is due")

    conv2 = client.post(f"{API}/inbox/customers/{not_opted['id']}/conversation", headers=h).json()
    no = client.post(f"{API}/inbox/conversations/{conv2['id']}/messages", headers=h, json=send)
    assert no.status_code == 409 and no.json()["detail"]["code"] == "no_opt_in"

    wrong = client.post(f"{API}/inbox/conversations/{conv['id']}/messages", headers=h,
                        json={**send, "template_params": ["only one"]})
    assert wrong.status_code == 409


def test_template_validation(client, company_a):
    h = company_a.h(Role.owner)
    connect_simulator(client, h)
    bad = client.post(f"{API}/whatsapp/templates", headers=h, json={"name": "x", "category": "UTILITY", "body": "Hi {{2}}"})
    assert bad.status_code == 422
    bad = client.post(f"{API}/whatsapp/templates", headers=h, json={"name": "Bad-Name!", "category": "UTILITY", "body": "Hi"})
    assert bad.status_code == 422


def test_stop_and_start(client, company_a):
    h = company_a.h(Role.owner)
    connect_simulator(client, h)
    c = add_customer(client, h, whatsapp_opt_in=True)
    sim_text(client, h, "9876500001", "STOP")
    cust = client.get(f"{API}/customers/{c['id']}", headers=h).json()
    assert cust["opted_out"] is True and cust["whatsapp_opt_in"] is False
    conv = conversations(client, h)[0]
    thread = messages(client, h, conv["id"])
    assert thread[-1]["sender"] == "system" and "unsubscribed" in thread[-1]["text"]
    blocked = client.post(f"{API}/inbox/conversations/{conv['id']}/messages", headers=h, json={"text": "Offer!"})
    assert blocked.status_code == 409 and blocked.json()["detail"]["code"] == "opted_out"

    sim_text(client, h, "9876500001", "start")
    assert client.get(f"{API}/customers/{c['id']}", headers=h).json()["opted_out"] is False


def test_status_updates_and_order(client, company_a, meta):
    """AC: delivered then read; out-of-order webhooks never move status backwards; failures keep the reason."""
    h = company_a.h(Role.owner)
    connect_cloud(client, h)
    add_customer(client, h)
    post_webhook(client, wa_payload("1000000001", [{"from": "919876500001", "id": "wamid.IN1", "timestamp": ts(),
                                                    "type": "text", "text": {"body": "hi"}}]))
    conv = conversations(client, h)[0]
    out = client.post(f"{API}/inbox/conversations/{conv['id']}/messages", headers=h, json={"text": "Hello"}).json()
    assert out["status"] == "sent"
    from app.core.database import SessionLocal

    with SessionLocal() as s:
        stored = s.get(Message, out["id"]).wa_message_id
    post_webhook(client, wa_payload("1000000001", statuses=[{"id": stored, "status": "read", "timestamp": ts()}]))
    post_webhook(client, wa_payload("1000000001", statuses=[{"id": stored, "status": "delivered", "timestamp": ts()}]))
    after = next(m for m in messages(client, h, conv["id"]) if m["id"] == out["id"])
    assert after["status"] == "read"

    out2 = client.post(f"{API}/inbox/conversations/{conv['id']}/messages", headers=h, json={"text": "Again"}).json()
    with SessionLocal() as s:
        stored2 = s.get(Message, out2["id"]).wa_message_id
    post_webhook(client, wa_payload("1000000001", statuses=[{"id": stored2, "status": "failed", "timestamp": ts(),
                                                             "errors": [{"code": 131026, "title": "Undeliverable",
                                                                         "error_data": {"details": "Receiver not on WhatsApp"}}]}]))
    failed = next(m for m in messages(client, h, conv["id"]) if m["id"] == out2["id"])
    assert failed["status"] == "failed" and failed["error_code"] == "131026" and "not on WhatsApp" in failed["error_message"]


def test_cloud_send_payload_and_errors(client, company_a, meta):
    h = company_a.h(Role.owner)
    connect_cloud(client, h)
    add_customer(client, h)
    post_webhook(client, wa_payload("1000000001", [{"from": "919876500001", "id": "wamid.IN2", "timestamp": ts(),
                                                    "type": "text", "text": {"body": "hi"}}]))
    conv = conversations(client, h)[0]
    client.post(f"{API}/inbox/conversations/{conv['id']}/messages", headers=h, json={
        "kind": "buttons", "text": "Confirm your order?",
        "buttons": [{"id": "confirm", "title": "Confirm"}, {"id": "cancel", "title": "Cancel"}]})
    body = meta.sent[-1]
    assert body["to"] == "919876500001" and body["messaging_product"] == "whatsapp"
    assert body["interactive"]["type"] == "button" and len(body["interactive"]["action"]["buttons"]) == 2

    # permanent Meta error -> failed at once, no retries
    meta.fail_with = (400, {"error": {"message": "Re-engagement message", "code": 131047}})
    calls = meta.calls
    r = client.post(f"{API}/inbox/conversations/{conv['id']}/messages", headers=h, json={"text": "x"}).json()
    assert r["status"] == "failed" and r["error_code"] == "131047" and meta.calls == calls + 1

    # temporary error -> 3 attempts, then failed
    meta.fail_with = (503, {"error": {"message": "Service unavailable", "code": 131000}})
    calls = meta.calls
    r = client.post(f"{API}/inbox/conversations/{conv['id']}/messages", headers=h, json={"text": "y"}).json()
    assert r["status"] == "failed" and meta.calls == calls + 3

    # retry from the inbox once Meta recovers
    meta.fail_with = None
    again = client.post(f"{API}/inbox/messages/{r['id']}/retry", headers=h).json()
    assert again["status"] == "sent"


def test_send_pdf_document(client, company_a, meta):
    h = company_a.h(Role.owner)
    connect_cloud(client, h)
    add_customer(client, h)
    post_webhook(client, wa_payload("1000000001", [{"from": "919876500001", "id": "wamid.IN3", "timestamp": ts(),
                                                    "type": "text", "text": {"body": "send invoice"}}]))
    conv = conversations(client, h)[0]
    r = client.post(f"{API}/inbox/conversations/{conv['id']}/media", headers=h,
                    files={"file": ("INV-1.pdf", b"%PDF-1.4 test", "application/pdf")}, data={"caption": "Your invoice"})
    assert r.status_code == 201 and r.json()["status"] == "sent"
    body = meta.sent[-1]
    assert body["type"] == "document" and body["document"]["id"] == "media-up-1"
    assert body["document"]["filename"] == "INV-1.pdf"
    bad = client.post(f"{API}/inbox/conversations/{conv['id']}/media", headers=h,
                      files={"file": ("x.exe", b"MZ", "application/x-msdownload")})
    assert bad.status_code == 400


def test_template_sync_from_meta(client, company_a, meta):
    h = company_a.h(Role.owner)
    connect_cloud(client, h)
    r = client.post(f"{API}/whatsapp/templates", headers=h, json={"name": "payment_reminder", "category": "UTILITY",
                                                                  "body": "Hi {{1}}", "example_values": ["Ravi"]})
    assert r.json()["status"] == "PENDING"
    synced = client.post(f"{API}/whatsapp/templates/sync", headers=h).json()
    assert synced[0]["status"] == "APPROVED"


def test_account_secrets_masked(client, company_a):
    h = company_a.h(Role.owner)
    out = connect_cloud(client, h, token="EAAverysecrettoken1234")
    assert out["access_token_masked"].endswith("1234") and "verysecret" not in json.dumps(out)
    # saving again without a token keeps the stored one
    r = client.put(f"{API}/whatsapp/account", headers=h, json={"mode": "cloud", "waba_id": "2000000001",
                                                               "phone_number_id": "1000000001"})
    assert r.status_code == 200 and r.json()["access_token_masked"].endswith("1234")


def test_inbox_permissions(client, company_a, company_b):
    h = company_a.h(Role.owner)
    connect_simulator(client, h)
    sales_id = str(company_a.users[Role.sales].id)
    add_customer(client, h, "9876500001", assigned_user_id=sales_id)
    add_customer(client, h, "9876500002")
    sim_text(client, h, "9876500001", "mine")
    sim_text(client, h, "9876500002", "not mine")

    assert len(conversations(client, h)) == 2
    sales_convs = conversations(client, company_a.h(Role.sales))
    assert len(sales_convs) == 1 and sales_convs[0]["last_message_preview"] == "mine"

    other = next(c for c in conversations(client, h) if c["last_message_preview"] == "not mine")
    assert client.get(f"{API}/inbox/conversations/{other['id']}/messages", headers=company_a.h(Role.sales)).status_code == 404
    # accounts can read but not reply
    assert len(conversations(client, company_a.h(Role.accounts))) == 2
    r = client.post(f"{API}/inbox/conversations/{other['id']}/messages", headers=company_a.h(Role.accounts), json={"text": "x"})
    assert r.status_code == 403
    # other tenant
    assert client.get(f"{API}/inbox/conversations/{other['id']}", headers=company_b.h(Role.owner)).status_code == 404


def test_button_reply_and_takeover(client, company_a):
    h = company_a.h(Role.owner)
    connect_simulator(client, h)
    add_customer(client, h)
    sim_text(client, h, "9876500001", "hi")
    conv = conversations(client, h)[0]
    too_many = client.post(f"{API}/inbox/conversations/{conv['id']}/messages", headers=h, json={
        "kind": "buttons", "text": "Pick", "buttons": [{"id": str(i), "title": f"B{i}"} for i in range(4)]})
    assert too_many.status_code == 409
    client.post(f"{API}/inbox/conversations/{conv['id']}/messages", headers=h, json={
        "kind": "buttons", "text": "Confirm?", "buttons": [{"id": "yes", "title": "Confirm"}]})
    r = client.post(f"{API}/simulator/inbound", headers=h, json={"from_number": "9876500001", "reply_id": "yes",
                                                                 "reply_title": "Confirm"}).json()
    assert r["msg_type"] == "interactive" and r["payload"]["reply_id"] == "yes" and r["text"] == "Confirm"
    # buyer replied, so our earlier message counts as read
    thread = messages(client, h, conv["id"])
    assert next(m for m in thread if m["direction"] == "outbound")["status"] == "read"

    off = client.patch(f"{API}/inbox/conversations/{conv['id']}", headers=h, json={"bot_enabled": False}).json()
    assert off["bot_enabled"] is False


def test_simulator_media_inbound(client, company_a):
    h = company_a.h(Role.owner)
    connect_simulator(client, h)
    r = client.post(f"{API}/simulator/inbound-media", headers=h, data={"from_number": "9876500005", "caption": "list"},
                    files={"file": ("order.jpg", b"\xff\xd8img", "image/jpeg")})
    assert r.status_code == 200 and r.json()["has_media"]
    assert client.get(f"{API}/media/{r.json()['id']}", headers=h).content == b"\xff\xd8img"


def test_simulator_blocked_in_cloud_mode(client, company_a, meta):
    h = company_a.h(Role.owner)
    connect_cloud(client, h)
    r = client.post(f"{API}/simulator/inbound", headers=h, json={"from_number": "9876500001", "text": "x"})
    assert r.status_code == 400


def test_lead_and_customer_records(client, company_a, db):
    """Inbound from an existing customer never creates a duplicate."""
    h = company_a.h(Role.owner)
    connect_simulator(client, h)
    add_customer(client, h, "9876500001")
    sim_text(client, h, "+91 98765 00001", "hi")
    count = db.scalar(select(func.count()).select_from(Customer).where(Customer.whatsapp_number == "+919876500001"))
    assert count == 1


def test_test_connection_subscribes_app(client, company_a, meta):
    """Test connection links the app to the WABA so Meta delivers message webhooks."""
    h = company_a.h(Role.owner)
    connect_cloud(client, h)
    r = client.post(f"{API}/whatsapp/account/test", headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["webhooks_subscribed"] is True and r.json()["subscribed_apps"] == ["Pine AI Commerce"]
    assert meta.subscribed
