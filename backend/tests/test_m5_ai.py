"""M5 AI order capture: flow with the mock provider, plus the real provider clients against mocked HTTP."""

import json
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import select

from app.core.config import settings
from app.models import AiLog, Direction, Message, Order, Role
from app.services.ai import matcher, providers
from app.services.ai.providers import AIError
from tests.test_m3_catalog import customer, product
from tests.test_m4_whatsapp import connect_simulator, sim_text

API = "/api/v1"
NUM = "9876500001"


@pytest.fixture
def shop(client, company_a):
    """Simulator connected, three products, one customer who has messaged us (window open)."""
    h = company_a.h(Role.owner)
    connect_simulator(client, h)
    atta = product(client, h, "ATTA-5KG", 255, name="Aashirvaad Atta 5kg", uom="BAG")
    soap = product(client, h, "SOAP-100", 38, name="Lifebuoy Soap 100g", uom="PCS")
    rice = product(client, h, "RICE-25", 1450, name="Ponni Rice 25kg", uom="BAG", aliases=["arisi", "ponni"])
    c = customer(client, h, contact_person="Murugan")
    sim_text(client, h, NUM, "hi")
    return {"h": h, "atta": atta, "soap": soap, "rice": rice, "customer": c, "client": client}


def say(shop, text):
    return sim_text(shop["client"], shop["h"], NUM, text)


def last_reply(db, customer_id) -> Message:
    db.expire_all()
    return db.scalars(select(Message).where(Message.customer_id == customer_id, Message.direction == Direction.outbound)
                      .order_by(Message.created_at.desc())).first()


def orders(db, customer_id):
    db.expire_all()
    return db.scalars(select(Order).where(Order.customer_id == customer_id).order_by(Order.created_at)).all()


def detail(shop, order_id):
    return shop["client"].get(f"{API}/orders/{order_id}", headers=shop["h"]).json()


def test_greeting_reply(shop, db):
    msg = last_reply(db, shop["customer"]["id"])
    assert msg.sender.value == "bot" and msg.text.startswith("Hi Murugan!")


def test_typed_order_becomes_quote(shop, db):
    """Typed list -> draft order matched to catalog -> priced quote with buttons; the AI never confirms."""
    inbound = say(shop, "10 bag atta, 5 box soap")
    [o] = orders(db, shop["customer"]["id"])
    assert o.status.value == "quoted" and o.source.value == "whatsapp" and str(o.source_message_id) == inbound["id"]
    d = detail(shop, o.id)
    lines = {l["sku"]: l for l in d["lines"]}
    assert lines["ATTA-5KG"]["quantity"] == "10.000" and lines["SOAP-100"]["quantity"] == "5.000"
    assert lines["ATTA-5KG"]["raw_text"] == "atta" and float(lines["ATTA-5KG"]["match_confidence"]) >= 0.8
    # prices from the pricing engine: 10 x 255 + 5 x 38 = 2740 + 5% GST
    assert d["grand_total"] == "2877.00"
    assert d["source_message"]["text"] == "10 bag atta, 5 box soap"
    quote = last_reply(db, shop["customer"]["id"])
    assert quote.msg_type.value == "interactive" and o.number in quote.text
    log = db.scalars(select(AiLog).where(AiLog.message_id == inbound["id"])).one()
    assert log.intent == "order" and log.order_id == o.id and log.provider == "mock" and log.error is None


def test_unclear_items_listed_separately(shop, db):
    say(shop, "10 atta\n4 zzqx powder")
    [o] = orders(db, shop["customer"]["id"])
    assert [l.sku for l in o.lines] == ["ATTA-5KG"]
    assert o.ai_unclear[0]["text"] == "zzqx powder"
    assert "Not added yet" in last_reply(db, shop["customer"]["id"]).text


def test_nothing_matched_asks_buyer(shop, db):
    say(shop, "5 zzqx powder")
    assert orders(db, shop["customer"]["id"]) == []
    assert "couldn't place this order" in last_reply(db, shop["customer"]["id"]).text


def test_ambiguous_product_needs_clarification(shop, db):
    product(shop["client"], shop["h"], "ATTA-10KG", 495, name="Aashirvaad Atta 10kg", uom="BAG")
    say(shop, "10 atta")
    assert orders(db, shop["customer"]["id"]) == []
    text = last_reply(db, shop["customer"]["id"]).text
    assert "did you mean" in text and "Atta 5kg" in text and "Atta 10kg" in text
    say(shop, "10 atta 10kg")
    [o] = orders(db, shop["customer"]["id"])
    assert o.lines[0].sku == "ATTA-10KG"


def test_alias_and_number_words(shop, db):
    say(shop, "pathu arisi")
    [o] = orders(db, shop["customer"]["id"])
    assert o.lines[0].sku == "RICE-25" and o.lines[0].quantity == Decimal("10.000")


def test_threshold_is_configurable(shop, db):
    shop["client"].patch(f"{API}/company", headers=shop["h"], json={"ai_match_threshold": "0.99"})
    say(shop, "10 atta")
    assert orders(db, shop["customer"]["id"]) == []


def test_buyer_corrections_update_the_quote(shop, db):
    """FR-M5-07: "make salt 30", "remove item 2" -> revised quote."""
    say(shop, "10 atta, 5 soap")
    [o] = orders(db, shop["customer"]["id"])
    say(shop, "make atta 20")
    d = detail(shop, o.id)
    assert d["revision"] == 2 and {l["sku"]: l["quantity"] for l in d["lines"]}["ATTA-5KG"] == "20.000"
    assert last_reply(db, shop["customer"]["id"]).text.startswith("Revised quote")
    assert any(e["actor"] == "buyer" and e["kind"] == "edit" for e in d["events"])
    say(shop, "remove item 2")
    say(shop, "add 3 rice")
    d = detail(shop, o.id)
    assert sorted(l["sku"] for l in d["lines"]) == ["ATTA-5KG", "RICE-25"]
    say(shop, "remove item 1, remove item 2")
    assert detail(shop, o.id)["status"] == "cancelled"


def test_confirm_button_still_works_after_ai_quote(shop, db):
    say(shop, "10 atta")
    [o] = orders(db, shop["customer"]["id"])
    shop["client"].post(f"{API}/simulator/inbound", headers=shop["h"],
                        json={"from_number": NUM, "reply_id": f"order:confirm:{o.id}"})
    assert detail(shop, o.id)["status"] == "confirmed"


def test_status_and_price_enquiry(shop, db):
    shop["client"].put(f"{API}/customers/{shop['customer']['id']}/prices", headers=shop["h"],
                       json={"items": [{"product_id": shop["atta"]["id"], "price": 240}]})
    say(shop, "atta rate evlo?")
    text = last_reply(db, shop["customer"]["id"]).text
    assert "₹240.00 per BAG" in text  # customer-specific price from M3, not from the AI
    say(shop, "where is my order")
    assert "couldn't find any recent orders" in last_reply(db, shop["customer"]["id"]).text


def test_other_intents_left_for_staff(shop, db):
    before = last_reply(db, shop["customer"]["id"]).id
    say(shop, "paid 5000 by gpay today")
    assert last_reply(db, shop["customer"]["id"]).id == before
    assert db.scalars(select(AiLog).where(AiLog.intent == "payment")).first() is not None


def test_ai_failure_acknowledges_once_and_keeps_message(shop, db, monkeypatch):
    """FR-M5-11."""
    def boom(*a, **k):
        raise AIError("timeout")
    monkeypatch.setattr(providers.MockProvider, "extract", boom)
    say(shop, "10 atta")
    say(shop, "5 soap")
    replies = db.scalars(select(Message).where(Message.customer_id == shop["customer"]["id"],
                                               Message.text == "Thanks! We've received your message. Our team will get back to you shortly.")).all()
    assert len(replies) == 1  # not repeated within 30 minutes
    errs = db.scalars(select(AiLog).where(AiLog.error.is_not(None))).all()
    assert len(errs) == 2 and errs[0].error == "timeout"
    assert orders(db, shop["customer"]["id"]) == []
    conv = shop["client"].get(f"{API}/inbox/conversations", headers=shop["h"]).json()["items"][0]
    assert conv["unread_count"] >= 2  # still waiting for staff


def test_unexpected_error_does_not_lose_message(shop, db, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("bug")
    monkeypatch.setattr(providers.MockProvider, "extract", boom)
    say(shop, "10 atta")
    assert "received your message" in last_reply(db, shop["customer"]["id"]).text


def test_photo_with_mock_goes_to_staff(shop, db):
    r = shop["client"].post(f"{API}/simulator/inbound-media", headers=shop["h"], data={"from_number": NUM},
                            files={"file": ("list.jpg", b"\xff\xd8img", "image/jpeg")})
    assert r.status_code == 200, r.text
    assert "received your message" in last_reply(db, shop["customer"]["id"]).text
    assert "can't read photos" in db.scalars(select(AiLog).where(AiLog.input_media == "image/jpeg")).one().error


def test_bot_off_or_ai_off(shop, db):
    client, h = shop["client"], shop["h"]
    client.patch(f"{API}/company", headers=h, json={"ai_ordering_enabled": False})
    say(shop, "10 atta")
    assert orders(db, shop["customer"]["id"]) == []
    say(shop, "order status")  # keyword status reply still works without AI
    assert "recent orders" in last_reply(db, shop["customer"]["id"]).text
    client.patch(f"{API}/company", headers=h, json={"ai_ordering_enabled": True})
    conv = client.get(f"{API}/inbox/conversations", headers=h).json()["items"][0]
    client.patch(f"{API}/inbox/conversations/{conv['id']}", headers=h, json={"bot_enabled": False})
    n = db.query(AiLog).count()
    say(shop, "10 atta")
    assert orders(db, shop["customer"]["id"]) == [] and db.query(AiLog).count() == n


def test_save_alias_and_staff_edit_keeps_wording(shop, db):
    say(shop, "10 arisi")
    [o] = orders(db, shop["customer"]["id"])
    client, h = shop["client"], shop["h"]
    # staff swaps the product; wording travels with the line
    r = client.put(f"{API}/orders/{o.id}/lines", headers=h,
                   json={"lines": [{"product_id": shop["atta"]["id"], "quantity": 10, "raw_text": "arisi"}]})
    assert r.status_code == 200 and r.json()["lines"][0]["raw_text"] == "arisi"
    say(shop, "remove item 1")  # cancels; make a fresh order for the alias check
    say(shop, "3 lifebuoy")
    o2 = orders(db, shop["customer"]["id"])[-1]
    lid = o2.lines[0].id
    assert client.post(f"{API}/orders/{o2.id}/lines/{lid}/save-alias", headers=h).status_code == 200
    soap = client.get(f"{API}/products/{shop['soap']['id']}", headers=h).json()
    assert "lifebuoy" in soap["aliases"]
    client.post(f"{API}/orders/{o2.id}/lines/{lid}/save-alias", headers=h)
    assert client.get(f"{API}/products/{shop['soap']['id']}", headers=h).json()["aliases"].count("lifebuoy") == 1


def test_usage_and_logs_api(shop, client, company_a):
    say(shop, "10 atta")
    h = shop["h"]
    u = client.get(f"{API}/ai/usage", headers=h).json()
    assert u["calls"] >= 2 and u["orders_created"] == 1 and u["by_intent"]["order"] == 1
    logs = client.get(f"{API}/ai/logs", headers=h).json()
    assert logs["items"][0]["order_number"] and logs["items"][0]["customer_name"] == "Shop"
    st = client.get(f"{API}/ai/status", headers=h).json()
    assert st["provider"] == "mock" and st["is_mock"] and st["ai_match_threshold"] == "0.80"
    assert client.get(f"{API}/ai/logs", headers=company_a.h(Role.sales)).status_code == 403


def test_matcher_scores():
    class P:
        def __init__(self, sku, name, aliases=()):
            self.sku, self.name, self.aliases, self.id = sku, name, list(aliases), sku
    ps = [P("ATTA-5KG", "Aashirvaad Atta 5kg"), P("SOAP", "Lifebuoy Soap"), P("SUGAR", "Sugar 1kg", ["cheeni"])]
    assert matcher.best_match("atta-5kg", ps)[1] == 1.0
    assert matcher.best_match("cheeni", ps)[0].sku == "SUGAR"
    assert matcher.best_match("lifeboy soap", ps)[0].sku == "SOAP"  # typo tolerated
    p, conf, _ = matcher.best_match("xyz", ps)
    assert conf < 0.8


# ---------------- real providers against mocked HTTP ----------------
@pytest.fixture
def http(monkeypatch):
    calls = []
    handler = {"fn": None}

    def route(request: httpx.Request):
        calls.append(request)
        return handler["fn"](request)
    monkeypatch.setattr(providers, "TRANSPORT", httpx.MockTransport(route))
    return calls, handler


def _use(monkeypatch, provider, stt="none"):
    monkeypatch.setattr(settings, "AI_PROVIDER", provider)
    monkeypatch.setattr(settings, "AI_API_KEY", "sk-test")
    monkeypatch.setattr(settings, "STT_PROVIDER", stt)


def test_anthropic_provider(shop, db, http, monkeypatch):
    calls, handler = http
    _use(monkeypatch, "anthropic")
    handler["fn"] = lambda req: httpx.Response(200, json={
        "model": "claude-haiku-5-5", "usage": {"input_tokens": 1200, "output_tokens": 150},
        "content": [{"type": "tool_use", "name": "record_message", "input": {
            "intent": "order", "language": "ta",
            "lines": [{"text": "rice", "quantity": 2, "unit": "bag", "sku": "RICE-25", "confidence": 0.95}],
            "changes": []}}]})
    say(shop, "rendu mootai ponni arisi anuppunga")
    req = calls[-1]
    body = json.loads(req.content)
    assert req.url.path == "/v1/messages" and req.headers["x-api-key"] == "sk-test"
    assert body["model"] == "claude-haiku-5-5" and body["tool_choice"]["name"] == "record_message"
    assert "RICE-25 | Ponni Rice 25kg" in body["messages"][0]["content"][-1]["text"]  # catalog given as context
    [o] = orders(db, shop["customer"]["id"])
    assert o.lines[0].sku == "RICE-25" and o.lines[0].quantity == Decimal("2.000")
    log = db.scalars(select(AiLog).where(AiLog.provider == "anthropic")).one()
    assert log.input_tokens == 1200 and log.cost_usd == Decimal("0.000195")


def test_anthropic_invented_sku_is_not_trusted(shop, db, http, monkeypatch):
    _, handler = http
    _use(monkeypatch, "anthropic")
    handler["fn"] = lambda req: httpx.Response(200, json={
        "model": "claude-haiku-5-5", "usage": {"input_tokens": 10, "output_tokens": 10},
        "content": [{"type": "tool_use", "input": {"intent": "order", "language": "en", "changes": [],
                                                   "lines": [{"text": "zzqx", "quantity": 1, "unit": None,
                                                              "sku": "FAKE-1", "confidence": 1}]}}]})
    say(shop, "1 zzqx")
    assert orders(db, shop["customer"]["id"]) == []


def test_openai_provider_with_photo_and_voice(shop, db, http, monkeypatch):
    calls, handler = http
    _use(monkeypatch, "openai", stt="openai")

    def fn(req: httpx.Request):
        if req.url.path.endswith("/audio/transcriptions"):
            return httpx.Response(200, json={"text": "anju soap venum"})
        body = json.loads(req.content)
        has_image = any(p.get("type") == "image_url" for p in body["messages"][1]["content"])
        lines = [{"text": "atta", "quantity": 3, "unit": "bag", "sku": "ATTA-5KG", "confidence": 0.9}] if has_image \
            else [{"text": "soap", "quantity": 5, "unit": None, "sku": "SOAP-100", "confidence": 0.9}]
        return httpx.Response(200, json={"model": body["model"], "usage": {"prompt_tokens": 900, "completion_tokens": 80},
                                         "choices": [{"message": {"content": json.dumps(
                                             {"intent": "order", "language": "en", "lines": lines, "changes": []})}}]})
    handler["fn"] = fn
    client, h = shop["client"], shop["h"]
    client.post(f"{API}/simulator/inbound-media", headers=h, data={"from_number": NUM},
                files={"file": ("list.jpg", b"\xff\xd8img", "image/jpeg")})
    img_call = json.loads(calls[-1].content)
    assert img_call["model"] == "gpt-5.4" and img_call["response_format"]["type"] == "json_schema"
    o1 = orders(db, shop["customer"]["id"])[-1]
    assert o1.lines[0].sku == "ATTA-5KG"
    say(shop, "remove item 1")  # cancel it so the voice note makes a new order, not a change
    client.post(f"{API}/simulator/inbound-media", headers=h, data={"from_number": NUM},
                files={"file": ("voice.ogg", b"OggS", "audio/ogg")})
    assert any(c.url.path.endswith("/audio/transcriptions") for c in calls)
    o2 = orders(db, shop["customer"]["id"])[-1]
    assert o2.id != o1.id and o2.lines[0].sku == "SOAP-100"
    src = detail(shop, o2.id)["source_message"]
    assert src["transcript"] == "anju soap venum" and src["msg_type"] == "audio"


def test_provider_http_error_is_handled(shop, db, http, monkeypatch):
    _, handler = http
    _use(monkeypatch, "openai")
    handler["fn"] = lambda req: httpx.Response(429, json={"error": {"message": "Rate limit reached"}})
    say(shop, "10 atta")
    assert "received your message" in last_reply(db, shop["customer"]["id"]).text
    assert "Rate limit" in db.scalars(select(AiLog).where(AiLog.provider == "openai")).one().error


def test_voice_without_stt_goes_to_staff(shop, db):
    shop["client"].post(f"{API}/simulator/inbound-media", headers=shop["h"], data={"from_number": NUM},
                        files={"file": ("voice.ogg", b"OggS", "audio/ogg")})
    assert "received your message" in last_reply(db, shop["customer"]["id"]).text
    assert "speech-to-text" in db.scalars(select(AiLog).where(AiLog.kind == "transcribe")).one().error
