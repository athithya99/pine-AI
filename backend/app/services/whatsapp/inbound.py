"""Turn Meta webhook payloads into stored messages (FR-M4-02, 03, 07, 10, 11).

Payload shape (Cloud API):
{"object": "whatsapp_business_account",
 "entry": [{"id": "<WABA_ID>", "changes": [{"field": "messages", "value": {
     "metadata": {"display_phone_number": "...", "phone_number_id": "..."},
     "contacts": [{"profile": {"name": "..."}, "wa_id": "9198..."}],
     "messages": [{"from": "9198...", "id": "wamid...", "timestamp": "1700000000", "type": "text", "text": {"body": "hi"}}],
     "statuses": [{"id": "wamid...", "status": "delivered", "timestamp": "...", "errors": [...]}]
 }}]}]}
"""

import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    Conversation,
    Customer,
    CustomerStatus,
    Direction,
    Message,
    MessageStatus,
    MessageType,
    SenderKind,
    WhatsAppAccount,
)
from app.models.whatsapp import STATUS_RANK

log = logging.getLogger("pinesphere.whatsapp")

STOP_WORDS = {"STOP", "UNSUBSCRIBE", "STOP ALL"}
START_WORDS = {"START", "SUBSCRIBE"}
MEDIA_TYPES = {"image", "audio", "video", "document", "sticker"}


def _ts(value: Any) -> datetime:
    try:
        return datetime.fromtimestamp(int(value), UTC)
    except (TypeError, ValueError):
        return datetime.now(UTC)


def get_or_create_conversation(db: Session, customer: Customer) -> Conversation:
    conv = db.scalar(select(Conversation).where(
        Conversation.tenant_id == customer.tenant_id, Conversation.customer_id == customer.id))
    if conv is None:
        conv = Conversation(tenant_id=customer.tenant_id, customer_id=customer.id)
        db.add(conv)
        db.flush()
    return conv


def _customer_for(db: Session, account: WhatsAppAccount, wa_from: str, profile_name: str | None) -> Customer:
    number = "+" + wa_from.lstrip("+")
    customer = db.scalar(select(Customer).where(
        Customer.tenant_id == account.tenant_id, Customer.whatsapp_number == number))
    if customer is None:
        # unknown number -> new lead for staff to complete or reject (FR-M2-07)
        customer = Customer(
            tenant_id=account.tenant_id,
            business_name=(profile_name or number)[:200],
            contact_person=profile_name,
            whatsapp_number=number,
            status=CustomerStatus.lead,
            tags=[],
        )
        db.add(customer)
        db.flush()
    return customer


def parse_message(m: dict) -> tuple[MessageType, str | None, dict | None, str | None, str | None]:
    """Return (type, text, payload, media_id, filename)."""
    t = m.get("type", "unsupported")
    if t == "text":
        return MessageType.text, m.get("text", {}).get("body"), None, None, None
    if t in MEDIA_TYPES:
        part = m.get(t, {})
        payload = {"mime_type": part.get("mime_type")}
        if t == "audio" and part.get("voice"):
            payload["voice"] = True
        return MessageType(t), part.get("caption"), payload, part.get("id"), part.get("filename")
    if t == "interactive":
        it = m.get("interactive", {})
        reply = it.get("button_reply") or it.get("list_reply") or {}
        return (MessageType.interactive, reply.get("title"),
                {"reply_type": it.get("type"), "reply_id": reply.get("id"), "description": reply.get("description")},
                None, None)
    if t == "button":  # quick-reply tap on a template message
        b = m.get("button", {})
        return MessageType.button, b.get("text"), {"reply_id": b.get("payload")}, None, None
    if t == "location":
        loc = m.get("location", {})
        label = loc.get("name") or loc.get("address") or f"{loc.get('latitude')}, {loc.get('longitude')}"
        return MessageType.location, label, loc, None, None
    return MessageType.unsupported, None, {"raw_type": t}, None, None


def preview_of(msg_type: MessageType, text: str | None) -> str:
    if text:
        return text[:200]
    return {
        MessageType.image: "📷 Photo", MessageType.audio: "🎤 Voice note", MessageType.video: "🎬 Video",
        MessageType.document: "📄 Document", MessageType.sticker: "Sticker", MessageType.location: "📍 Location",
    }.get(msg_type, "Message")


def handle_inbound(db: Session, account: WhatsAppAccount, m: dict, names: dict[str, str]) -> Message | None:
    wamid = m.get("id")
    if wamid and db.scalar(select(Message.id).where(
            Message.tenant_id == account.tenant_id, Message.wa_message_id == wamid)):
        return None  # duplicate delivery from Meta (FR-M4-11)

    customer = _customer_for(db, account, m.get("from", ""), names.get(m.get("from", "")))
    conv = get_or_create_conversation(db, customer)
    msg_type, text, payload, media_id, filename = parse_message(m)
    when = _ts(m.get("timestamp"))

    if media_id:
        payload = {**(payload or {}), "media_id": media_id}
    msg = Message(
        tenant_id=account.tenant_id, conversation_id=conv.id, customer_id=customer.id,
        direction=Direction.inbound, msg_type=msg_type, text=text, payload=payload,
        media_filename=filename, media_mime=(payload or {}).get("mime_type"),
        wa_message_id=wamid, status=MessageStatus.received, sender=SenderKind.customer, wa_timestamp=when,
    )
    db.add(msg)

    # buyer replying implies they've seen our earlier messages
    for prior in db.scalars(select(Message).where(
            Message.conversation_id == conv.id, Message.direction == Direction.outbound,
            Message.status.in_([MessageStatus.sent, MessageStatus.delivered]))):
        prior.status = MessageStatus.read

    conv.last_inbound_at = when
    conv.last_message_at = when
    conv.last_message_preview = preview_of(msg_type, text)
    conv.unread_count = (conv.unread_count or 0) + 1

    word = (text or "").strip().upper() if msg_type == MessageType.text else ""
    if word in STOP_WORDS:
        customer.opted_out = True
        customer.whatsapp_opt_in = False
        msg.payload = {**(msg.payload or {}), "opt_out": True}
    elif word in START_WORDS and customer.opted_out:
        customer.opted_out = False
        customer.whatsapp_opt_in = True
        customer.opt_in_at = when
        customer.opt_in_source = "whatsapp_start"
    db.flush()
    return msg


def handle_status(db: Session, account: WhatsAppAccount, s: dict) -> None:
    msg = db.scalar(select(Message).where(
        Message.tenant_id == account.tenant_id, Message.wa_message_id == s.get("id")))
    if msg is None:
        return
    try:
        new = MessageStatus(s.get("status"))
    except ValueError:
        return
    if new == MessageStatus.failed:
        err = (s.get("errors") or [{}])[0]
        msg.status = MessageStatus.failed
        msg.error_code = str(err.get("code") or "")
        msg.error_message = (err.get("error_data", {}).get("details") or err.get("message") or err.get("title") or "")[:500]
    elif STATUS_RANK.get(new, -1) > STATUS_RANK.get(msg.status, -1):  # never move backwards (webhooks can arrive out of order)
        msg.status = new
    msg.status_updated_at = _ts(s.get("timestamp"))


def process_payload(db: Session, payload: dict) -> list[Message]:
    """Process a whole webhook payload. Returns new inbound messages (for media download / bot)."""
    created: list[Message] = []
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            if change.get("field") != "messages":
                continue
            value = change.get("value", {})
            pnid = value.get("metadata", {}).get("phone_number_id")
            account = db.scalar(select(WhatsAppAccount).where(
                WhatsAppAccount.phone_number_id == pnid, WhatsAppAccount.is_active.is_(True)))
            if account is None:
                log.warning("Webhook for unknown phone_number_id %s ignored", pnid)
                continue
            names = {c.get("wa_id"): c.get("profile", {}).get("name") for c in value.get("contacts", [])}
            for m in value.get("messages", []):
                msg = handle_inbound(db, account, m, names)
                if msg is not None:
                    created.append(msg)
            for s in value.get("statuses", []):
                handle_status(db, account, s)
    return created
