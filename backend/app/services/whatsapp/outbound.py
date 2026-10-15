"""Queue and send WhatsApp messages with Meta's rules enforced (FR-M4-04, 06, 07, 10; FR-M2-06).

Callers use queue_*() to create the Message row; the actual API call runs in a background task
(deliver), with up to 3 attempts for temporary failures (NFR-09).
"""

import re
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import (
    Conversation,
    Customer,
    Direction,
    Message,
    MessageStatus,
    MessageTemplate,
    MessageType,
    SenderKind,
    TemplateStatus,
    User,
    WhatsAppAccount,
)
from app.services import media
from app.services.whatsapp.client import WhatsAppError, get_client
from app.services.whatsapp.inbound import get_or_create_conversation, preview_of

MAX_ATTEMPTS = 3
# Meta error codes worth retrying: rate limits, temporary service errors
RETRYABLE_CODES = {"4", "80007", "130429", "131000", "131016", "131048", "131056", "500", "502", "503", "504"}


class SendBlocked(Exception):
    """Business rule stops this send; the message is not created."""

    def __init__(self, message: str, code: str):
        super().__init__(message)
        self.code = code
        self.message = message


def get_account(db: Session, tenant_id: uuid.UUID) -> WhatsAppAccount:
    acc = db.scalar(select(WhatsAppAccount).where(WhatsAppAccount.tenant_id == tenant_id))
    if acc is None or not acc.is_active:
        raise SendBlocked("Connect a WhatsApp number in Settings → WhatsApp first", "not_connected")
    return acc


def window_open(conv: Conversation | None, now: datetime | None = None) -> bool:
    if conv is None or conv.last_inbound_at is None:
        return False
    now = now or datetime.now(UTC)
    return now - conv.last_inbound_at < timedelta(hours=settings.WHATSAPP_WINDOW_HOURS)


def window_closes_at(conv: Conversation | None) -> datetime | None:
    if conv is None or conv.last_inbound_at is None:
        return None
    return conv.last_inbound_at + timedelta(hours=settings.WHATSAPP_WINDOW_HOURS)


def render_template(tpl: MessageTemplate, params: list[str]) -> str:
    def sub(m: re.Match) -> str:
        i = int(m.group(1)) - 1
        return params[i] if 0 <= i < len(params) else m.group(0)

    text = re.sub(r"\{\{(\d+)\}\}", sub, tpl.body)
    parts = [p for p in (tpl.header_text, text, tpl.footer) if p]
    return "\n\n".join(parts)


def _create(db: Session, customer: Customer, conv: Conversation, *, msg_type: MessageType, text: str | None,
            payload: dict, sender: SenderKind, user: User | None, media_path: str | None = None,
            media_mime: str | None = None, media_filename: str | None = None) -> Message:
    msg = Message(
        tenant_id=customer.tenant_id, conversation_id=conv.id, customer_id=customer.id,
        direction=Direction.outbound, msg_type=msg_type, text=text, payload=payload,
        media_path=media_path, media_mime=media_mime, media_filename=media_filename,
        status=MessageStatus.queued, sender=sender, sent_by_user_id=user.id if user else None,
    )
    db.add(msg)
    now = datetime.now(UTC)
    conv.last_message_at = now
    conv.last_message_preview = preview_of(msg_type, text if msg_type != MessageType.template else text)
    db.flush()
    return msg


def _check_session_send(db: Session, customer: Customer) -> Conversation:
    """Free-form messages: only inside the 24-hour customer service window."""
    get_account(db, customer.tenant_id)
    if customer.opted_out:
        raise SendBlocked("This customer has opted out of WhatsApp messages", "opted_out")
    conv = get_or_create_conversation(db, customer)
    if not window_open(conv):
        raise SendBlocked(
            "More than 24 hours since the customer's last message. Use an approved template to start the conversation.",
            "window_closed",
        )
    return conv


def queue_text(db: Session, customer: Customer, text: str, *, sender: SenderKind = SenderKind.user,
               user: User | None = None) -> Message:
    conv = _check_session_send(db, customer)
    return _create(db, customer, conv, msg_type=MessageType.text, text=text,
                   payload={"type": "text", "text": {"body": text, "preview_url": False}}, sender=sender, user=user)


def queue_buttons(db: Session, customer: Customer, body: str, buttons: list[dict], *,
                  sender: SenderKind = SenderKind.user, user: User | None = None) -> Message:
    """buttons: [{"id": "confirm", "title": "Confirm"}] - max 3, titles max 20 chars."""
    if not 1 <= len(buttons) <= 3:
        raise SendBlocked("Reply buttons must be between 1 and 3", "invalid")
    if any(len(b["title"]) > 20 for b in buttons):
        raise SendBlocked("Button titles can be at most 20 characters", "invalid")
    conv = _check_session_send(db, customer)
    payload = {"type": "interactive", "interactive": {
        "type": "button", "body": {"text": body},
        "action": {"buttons": [{"type": "reply", "reply": {"id": b["id"], "title": b["title"]}} for b in buttons]},
    }}
    return _create(db, customer, conv, msg_type=MessageType.interactive, text=body, payload=payload,
                   sender=sender, user=user)


def queue_list(db: Session, customer: Customer, body: str, button_label: str, rows: list[dict], *,
               section_title: str = "Options", sender: SenderKind = SenderKind.user,
               user: User | None = None) -> Message:
    """rows: [{"id": "...", "title": "...", "description": "..."}] - max 10 rows."""
    if not 1 <= len(rows) <= 10:
        raise SendBlocked("A list can have 1 to 10 rows", "invalid")
    if len(button_label) > 20 or any(len(r["title"]) > 24 for r in rows):
        raise SendBlocked("List button label max 20 characters, row titles max 24", "invalid")
    conv = _check_session_send(db, customer)
    payload = {"type": "interactive", "interactive": {
        "type": "list", "body": {"text": body},
        "action": {"button": button_label, "sections": [{"title": section_title[:24], "rows": [
            {k: v for k, v in {"id": r["id"], "title": r["title"],
                               "description": (r.get("description") or "")[:72]}.items() if v}
            for r in rows]}]},
    }}
    return _create(db, customer, conv, msg_type=MessageType.interactive, text=body, payload=payload,
                   sender=sender, user=user)


def queue_media(db: Session, customer: Customer, content: bytes, mime: str, filename: str,
                caption: str | None = None, *, sender: SenderKind = SenderKind.user,
                user: User | None = None) -> Message:
    conv = _check_session_send(db, customer)
    kind = "image" if mime in {"image/jpeg", "image/png"} else "document"
    path = media.save(customer.tenant_id, content, mime, filename)
    part: dict[str, Any] = {}
    if caption:
        part["caption"] = caption
    if kind == "document":
        part["filename"] = filename
    return _create(db, customer, conv, msg_type=MessageType(kind), text=caption,
                   payload={"type": kind, kind: part}, sender=sender, user=user,
                   media_path=path, media_mime=mime, media_filename=filename)


def queue_template(db: Session, customer: Customer, template: MessageTemplate, params: list[str], *,
                   sender: SenderKind = SenderKind.system, user: User | None = None) -> Message:
    get_account(db, customer.tenant_id)
    if template.tenant_id != customer.tenant_id:
        raise SendBlocked("Template not found", "invalid")
    if template.status != TemplateStatus.approved:
        raise SendBlocked(f"Template '{template.name}' is not approved by WhatsApp yet", "template_not_approved")
    if customer.opted_out:
        raise SendBlocked("This customer has opted out of WhatsApp messages", "opted_out")
    conv = get_or_create_conversation(db, customer)
    # business-initiated message (outside the window) needs recorded opt-in (FR-M2-06)
    if not window_open(conv) and not customer.whatsapp_opt_in:
        raise SendBlocked("This customer has not opted in to WhatsApp messages", "no_opt_in")
    if len(params) != template.variable_count:
        raise SendBlocked(f"Template needs {template.variable_count} value(s), got {len(params)}", "invalid")
    components = []
    if params:
        components.append({"type": "body", "parameters": [{"type": "text", "text": p} for p in params]})
    payload = {"type": "template", "template": {
        "name": template.name, "language": {"code": template.language}, "components": components,
    }, "_template_id": str(template.id), "_buttons": template.buttons or []}
    return _create(db, customer, conv, msg_type=MessageType.template, text=render_template(template, params),
                   payload=payload, sender=sender, user=user)


def deliver(db: Session, message_id: uuid.UUID, attempt: int = 1) -> bool:
    """Send a queued message. Returns True if another attempt should be scheduled."""
    msg = db.get(Message, message_id)
    if msg is None or msg.status != MessageStatus.queued:
        return False
    customer = db.get(Customer, msg.customer_id)
    account = db.scalar(select(WhatsAppAccount).where(WhatsAppAccount.tenant_id == msg.tenant_id))
    try:
        if account is None:
            raise WhatsAppError("WhatsApp number disconnected", "not_connected")
        client = get_client(account)
        body = {k: v for k, v in (msg.payload or {}).items() if not k.startswith("_")}
        if msg.media_path and msg.msg_type in (MessageType.image, MessageType.document):
            kind = msg.msg_type.value
            media_id = client.upload_media(media.read(msg.media_path), msg.media_mime or "application/octet-stream",
                                           msg.media_filename or "file")
            body[kind] = {**body.get(kind, {}), "id": media_id}
        msg.wa_message_id = client.send(customer.whatsapp_number, body)
        msg.status = MessageStatus.sent
        msg.status_updated_at = datetime.now(UTC)
        msg.error_code = msg.error_message = None
        db.commit()
        return False
    except WhatsAppError as exc:
        retry = (exc.code in RETRYABLE_CODES) and attempt < MAX_ATTEMPTS
        msg.error_code = exc.code
        msg.error_message = exc.message[:500]
        if not retry:
            msg.status = MessageStatus.failed
        db.commit()
        return retry
    except Exception as exc:  # network errors etc.
        retry = attempt < MAX_ATTEMPTS
        msg.error_message = str(exc)[:500]
        if not retry:
            msg.status = MessageStatus.failed
        db.commit()
        return retry
