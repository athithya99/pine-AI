import re
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.api.v1.customers import get_customer_or_404
from app.core.config import settings
from app.core.crypto import decrypt, encrypt, mask
from app.core.database import get_db
from app.core.deps import ALL_STAFF, MANAGERS, require_roles
from app.models import (
    Conversation,
    Customer,
    Direction,
    Message,
    MessageStatus,
    MessageTemplate,
    Role,
    SenderKind,
    TemplateStatus,
    User,
    WaMode,
    WhatsAppAccount,
)
from app.schemas.common import Page
from app.schemas.whatsapp import (
    AccountIn,
    AccountOut,
    ConversationOut,
    ConversationPatch,
    MessageOut,
    SendIn,
    SimInbound,
    SimStatus,
    TemplateIn,
    TemplateOut,
)
from app.services import audit, media
from app.services.whatsapp import outbound
from app.services.whatsapp.client import WhatsAppError, get_client
from app.services.whatsapp.inbound import get_or_create_conversation, handle_status, process_payload
from app.worker import tasks

router = APIRouter(tags=["WhatsApp"])
staff = require_roles(*ALL_STAFF)
managers = require_roles(*MANAGERS)
senders = require_roles(Role.owner, Role.admin, Role.sales)  # Accounts can view the inbox but not reply

MAX_MEDIA_BYTES = 16 * 1024 * 1024
ALLOWED_MEDIA = {"image/jpeg", "image/png", "application/pdf"}


def _account(db: Session, tenant_id: uuid.UUID) -> WhatsAppAccount | None:
    return db.scalar(select(WhatsAppAccount).where(WhatsAppAccount.tenant_id == tenant_id))


def _account_out(acc: WhatsAppAccount | None) -> AccountOut:
    if acc is None:
        return AccountOut(connected=False, simulator_enabled=settings.ENABLE_SIMULATOR)
    return AccountOut(
        connected=acc.is_active, mode=acc.mode, waba_id=acc.waba_id, phone_number_id=acc.phone_number_id,
        display_phone_number=acc.display_phone_number,
        access_token_masked=mask(decrypt(acc.access_token_enc)), app_secret_masked=mask(decrypt(acc.app_secret_enc)),
        verify_token_hint=mask(settings.WHATSAPP_VERIFY_TOKEN), simulator_enabled=settings.ENABLE_SIMULATOR,
    )


# ========================= account =========================
@router.get("/whatsapp/account", response_model=AccountOut)
def get_account(db: Session = Depends(get_db), user: User = Depends(staff)):
    return _account_out(_account(db, user.tenant_id))


@router.put("/whatsapp/account", response_model=AccountOut)
def save_account(body: AccountIn, db: Session = Depends(get_db), user: User = Depends(managers)):
    if body.mode == WaMode.simulator and not settings.ENABLE_SIMULATOR:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Simulator mode is disabled on this server")
    acc = _account(db, user.tenant_id)
    if body.phone_number_id:
        clash = db.scalar(select(WhatsAppAccount.id).where(
            WhatsAppAccount.phone_number_id == body.phone_number_id, WhatsAppAccount.tenant_id != user.tenant_id))
        if clash:
            raise HTTPException(status.HTTP_409_CONFLICT, "This phone number ID is connected to another company")
    if acc is None:
        acc = WhatsAppAccount(tenant_id=user.tenant_id)
        db.add(acc)
    acc.mode = body.mode
    if body.mode == WaMode.simulator:
        # simulator gets a fake id so webhooks route the same way as a real number
        acc.phone_number_id = acc.phone_number_id if (acc.phone_number_id or "").startswith("sim") else f"sim{uuid.uuid4().int % 10**12}"
        acc.display_phone_number = body.display_phone_number or "Simulator"
        acc.waba_id = None
    else:
        acc.waba_id, acc.phone_number_id = body.waba_id, body.phone_number_id
        acc.display_phone_number = body.display_phone_number
        if body.access_token:
            acc.access_token_enc = encrypt(body.access_token.strip())
        if body.app_secret:
            acc.app_secret_enc = encrypt(body.app_secret.strip())
        if not acc.access_token_enc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Access token is required for a live number")
    acc.is_active = True
    db.flush()
    audit.record(db, user, "whatsapp_account", acc.id, "update",
                 new={"mode": acc.mode.value, "phone_number_id": acc.phone_number_id, "waba_id": acc.waba_id})
    db.commit()
    return _account_out(acc)


@router.post("/whatsapp/account/test")
def test_account(db: Session = Depends(get_db), user: User = Depends(managers)):
    """Check the saved credentials and make sure Meta will deliver message webhooks to this app."""
    acc = _account(db, user.tenant_id)
    if acc is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "No WhatsApp number saved yet")
    client = get_client(acc)
    try:
        templates = client.fetch_templates()
    except WhatsAppError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Meta rejected the credentials: {exc.message}") from None
    try:
        client.subscribe_app()
        apps = client.subscribed_apps()
    except WhatsAppError as exc:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Credentials work, but linking the app to your WhatsApp account failed: {exc.message}",
        ) from None
    return {"ok": True, "templates_found": len(templates), "webhooks_subscribed": True,
            "subscribed_apps": [a.get("name") for a in apps]}


# ========================= templates =========================
@router.get("/whatsapp/templates", response_model=list[TemplateOut])
def list_templates(approved: bool | None = None, db: Session = Depends(get_db), user: User = Depends(staff)):
    stmt = select(MessageTemplate).where(MessageTemplate.tenant_id == user.tenant_id)
    if approved:
        stmt = stmt.where(MessageTemplate.status == TemplateStatus.approved)
    return db.scalars(stmt.order_by(MessageTemplate.created_at.desc())).all()


@router.post("/whatsapp/templates", response_model=TemplateOut, status_code=status.HTTP_201_CREATED)
def create_template(body: TemplateIn, db: Session = Depends(get_db), user: User = Depends(managers)):
    """Create a template and submit it to Meta for approval (FR-M4-05)."""
    if db.scalar(select(MessageTemplate.id).where(
            MessageTemplate.tenant_id == user.tenant_id, MessageTemplate.name == body.name,
            MessageTemplate.language == body.language)):
        raise HTTPException(status.HTTP_409_CONFLICT, "A template with this name and language already exists")
    acc = _account(db, user.tenant_id)
    if acc is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Connect a WhatsApp number first")
    tpl = MessageTemplate(
        tenant_id=user.tenant_id, name=body.name, language=body.language, category=body.category,
        header_text=body.header_text, body=body.body, footer=body.footer,
        buttons=[b.model_dump() for b in body.buttons] or None, example_values=body.example_values or None,
        variable_count=len(set(re.findall(r"\{\{(\d+)\}\}", body.body))),
    )
    try:
        tpl.meta_template_id, meta_status = get_client(acc).submit_template(tpl)
        tpl.status = TemplateStatus(meta_status) if meta_status in TemplateStatus._value2member_map_ else TemplateStatus.pending
    except WhatsAppError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Meta rejected the template: {exc.message}") from None
    db.add(tpl)
    db.flush()
    audit.record(db, user, "message_template", tpl.id, "create", new=audit.snapshot(tpl))
    db.commit()
    return tpl


@router.post("/whatsapp/templates/sync", response_model=list[TemplateOut])
def sync_templates(db: Session = Depends(get_db), user: User = Depends(managers)):
    """Pull approval status from Meta."""
    acc = _account(db, user.tenant_id)
    if acc is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Connect a WhatsApp number first")
    try:
        remote = get_client(acc).fetch_templates()
    except WhatsAppError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, exc.message) from None
    by_key = {(r.get("name"), r.get("language")): r for r in remote}
    templates = db.scalars(select(MessageTemplate).where(MessageTemplate.tenant_id == user.tenant_id)).all()
    for t in templates:
        r = by_key.get((t.name, t.language))
        if r and r.get("status") in TemplateStatus._value2member_map_:
            t.status = TemplateStatus(r["status"])
            t.meta_template_id = r.get("id") or t.meta_template_id
            reason = r.get("rejected_reason")
            t.rejection_reason = reason if reason and reason != "NONE" else None
    db.commit()
    return templates


# ========================= inbox =========================
def _scoped_conversations(user: User):
    stmt = select(Conversation, Customer).join(Customer, Customer.id == Conversation.customer_id).where(
        Conversation.tenant_id == user.tenant_id)
    if user.role == Role.sales:
        stmt = stmt.where(Customer.assigned_user_id == user.id)
    return stmt


def _conv_out(conv: Conversation, c: Customer) -> ConversationOut:
    return ConversationOut(
        id=conv.id, customer_id=c.id, customer_name=c.business_name, whatsapp_number=c.whatsapp_number,
        customer_status=c.status.value, assigned_user_id=c.assigned_user_id, opted_out=c.opted_out,
        whatsapp_opt_in=c.whatsapp_opt_in, bot_enabled=conv.bot_enabled, unread_count=conv.unread_count,
        last_message_at=conv.last_message_at, last_message_preview=conv.last_message_preview,
        window_open=outbound.window_open(conv), window_closes_at=outbound.window_closes_at(conv),
    )


def _get_conversation(db: Session, user: User, conv_id: uuid.UUID) -> tuple[Conversation, Customer]:
    row = db.execute(_scoped_conversations(user).where(Conversation.id == conv_id)).first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversation not found")
    return row[0], row[1]


@router.get("/inbox/conversations", response_model=Page[ConversationOut])
def list_conversations(
    q: str | None = None,
    unread: bool | None = None,
    assigned_user_id: uuid.UUID | None = None,
    page: int = Query(default=1, ge=1),
    size: int = Query(default=30, ge=1, le=100),
    db: Session = Depends(get_db),
    user: User = Depends(staff),
):
    stmt = _scoped_conversations(user).where(Conversation.last_message_at.is_not(None))
    if q:
        stmt = stmt.where(or_(Customer.business_name.ilike(f"%{q}%"), Customer.whatsapp_number.ilike(f"%{q}%")))
    if unread:
        stmt = stmt.where(Conversation.unread_count > 0)
    if assigned_user_id:
        stmt = stmt.where(Customer.assigned_user_id == assigned_user_id)
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = db.execute(stmt.order_by(Conversation.last_message_at.desc()).offset((page - 1) * size).limit(size)).all()
    return Page(items=[_conv_out(conv, c) for conv, c in rows], total=total, page=page, size=size)


@router.post("/inbox/customers/{customer_id}/conversation", response_model=ConversationOut)
def open_conversation(customer_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(staff)):
    """Open (or create) the thread for a customer, e.g. from the customer page."""
    customer = get_customer_or_404(db, user, customer_id)
    conv = get_or_create_conversation(db, customer)
    db.commit()
    return _conv_out(conv, customer)


@router.get("/inbox/conversations/{conv_id}", response_model=ConversationOut)
def get_conversation(conv_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(staff)):
    conv, c = _get_conversation(db, user, conv_id)
    return _conv_out(conv, c)


@router.get("/inbox/conversations/{conv_id}/messages", response_model=list[MessageOut])
def list_messages(
    conv_id: uuid.UUID,
    before: datetime | None = None,
    limit: int = Query(default=50, le=200),
    mark_read: bool = True,
    db: Session = Depends(get_db),
    user: User = Depends(staff),
):
    conv, _ = _get_conversation(db, user, conv_id)
    stmt = select(Message).where(Message.conversation_id == conv.id)
    if before:
        stmt = stmt.where(Message.created_at < before)
    msgs = list(reversed(db.scalars(stmt.order_by(Message.created_at.desc()).limit(limit)).all()))
    if mark_read and conv.unread_count:
        conv.unread_count = 0
        db.commit()
        last_in = next((m for m in reversed(msgs) if m.direction == Direction.inbound), None)
        if last_in:
            tasks.mark_read.delay(str(last_in.id))
    out = []
    for m in msgs:
        o = MessageOut.model_validate(m)
        o.has_media = bool(m.media_path)
        o.payload = {k: v for k, v in (m.payload or {}).items() if k != "media_id"} or None
        out.append(o)
    return out


def _queue_and_send(db: Session, fn, *args, **kwargs) -> Message:
    try:
        msg = fn(db, *args, **kwargs)
    except outbound.SendBlocked as exc:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, {"code": exc.code, "message": exc.message}) from None
    db.commit()
    tasks.send_message.delay(str(msg.id))
    db.refresh(msg)
    return msg


def _msg_out(m: Message) -> MessageOut:
    o = MessageOut.model_validate(m)
    o.has_media = bool(m.media_path)
    return o


@router.post("/inbox/conversations/{conv_id}/messages", response_model=MessageOut, status_code=status.HTTP_201_CREATED)
def send(conv_id: uuid.UUID, body: SendIn, db: Session = Depends(get_db), user: User = Depends(senders)):
    _, customer = _get_conversation(db, user, conv_id)
    if body.kind == "text":
        msg = _queue_and_send(db, outbound.queue_text, customer, body.text.strip(), user=user)
    elif body.kind == "buttons":
        msg = _queue_and_send(db, outbound.queue_buttons, customer, body.text,
                              [b.model_dump() for b in body.buttons], user=user)
    elif body.kind == "list":
        msg = _queue_and_send(db, outbound.queue_list, customer, body.text, body.list_button,
                              [r.model_dump() for r in body.list_rows], user=user)
    else:
        tpl = db.scalar(select(MessageTemplate).where(
            MessageTemplate.id == body.template_id, MessageTemplate.tenant_id == user.tenant_id))
        if tpl is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Template not found")
        msg = _queue_and_send(db, outbound.queue_template, customer, tpl, body.template_params,
                              sender=SenderKind.user, user=user)
    return _msg_out(msg)


@router.post("/inbox/conversations/{conv_id}/media", response_model=MessageOut, status_code=status.HTTP_201_CREATED)
async def send_media(conv_id: uuid.UUID, file: UploadFile = File(...), caption: str | None = Form(default=None),
                     db: Session = Depends(get_db), user: User = Depends(senders)):
    _, customer = _get_conversation(db, user, conv_id)
    content = await file.read()
    mime = file.content_type or "application/octet-stream"
    if mime not in ALLOWED_MEDIA:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Only JPG, PNG and PDF files can be sent")
    if len(content) > MAX_MEDIA_BYTES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "File is larger than 16 MB")
    msg = _queue_and_send(db, outbound.queue_media, customer, content, mime, file.filename or "file", caption, user=user)
    return _msg_out(msg)


@router.patch("/inbox/conversations/{conv_id}", response_model=ConversationOut)
def update_conversation(conv_id: uuid.UUID, body: ConversationPatch, db: Session = Depends(get_db),
                        user: User = Depends(senders)):
    """Human takeover: switch the bot off for this thread, or back on (FR-M4-09)."""
    conv, c = _get_conversation(db, user, conv_id)
    conv.bot_enabled = body.bot_enabled
    audit.record(db, user, "conversation", conv.id, "update", new={"bot_enabled": body.bot_enabled})
    db.commit()
    return _conv_out(conv, c)


@router.post("/inbox/messages/{message_id}/retry", response_model=MessageOut)
def retry_message(message_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(senders)):
    msg = db.scalar(select(Message).where(Message.id == message_id, Message.tenant_id == user.tenant_id))
    if msg is None or msg.direction != Direction.outbound:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Message not found")
    _get_conversation(db, user, msg.conversation_id)  # access check for Sales
    if msg.status != MessageStatus.failed:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Only failed messages can be retried")
    msg.status = MessageStatus.queued
    msg.error_code = msg.error_message = None
    db.commit()
    tasks.send_message.delay(str(msg.id))
    db.refresh(msg)
    return _msg_out(msg)


@router.get("/media/{message_id}")
def get_media(message_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(staff)):
    msg = db.scalar(select(Message).where(Message.id == message_id, Message.tenant_id == user.tenant_id))
    if msg is None or not msg.media_path:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "File not found")
    _get_conversation(db, user, msg.conversation_id)
    return FileResponse(media.full_path(msg.media_path), media_type=msg.media_mime or "application/octet-stream",
                        filename=msg.media_filename or None)


# ========================= simulator (dev / demo only) =========================
def _sim_guard(db: Session, user: User) -> WhatsAppAccount:
    if not settings.ENABLE_SIMULATOR:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Simulator is disabled")
    acc = _account(db, user.tenant_id)
    if acc is None or acc.mode != WaMode.simulator:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Switch WhatsApp to simulator mode to use this")
    return acc


def _sim_payload(acc: WhatsAppAccount, from_number: str, name: str | None, message: dict) -> dict:
    wa_from = from_number.lstrip("+")
    message = {"from": wa_from, "id": f"wamid.SIMIN{uuid.uuid4().hex}",
               "timestamp": str(int(datetime.now(UTC).timestamp())), **message}
    return {"object": "whatsapp_business_account", "entry": [{"id": "sim", "changes": [{"field": "messages", "value": {
        "messaging_product": "whatsapp",
        "metadata": {"display_phone_number": acc.display_phone_number, "phone_number_id": acc.phone_number_id},
        "contacts": [{"profile": {"name": name or wa_from}, "wa_id": wa_from}],
        "messages": [message],
    }}]}]}


def _run_sim(db: Session, payload: dict) -> MessageOut:
    created = process_payload(db, payload)
    db.commit()
    for m in created:
        tasks.after_inbound(m)
    if not created:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Nothing was received")
    db.refresh(created[0])
    return _msg_out(created[0])


@router.post("/simulator/inbound", response_model=MessageOut)
def simulate_inbound(body: SimInbound, db: Session = Depends(get_db), user: User = Depends(managers)):
    """Pretend a buyer sent a text, or tapped one of our buttons / list rows."""
    from app.core.validators import normalize_phone

    acc = _sim_guard(db, user)
    try:
        number = normalize_phone(body.from_number)
    except ValueError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid phone number") from None
    if body.reply_id:
        message = {"type": "interactive", "interactive": {"type": "button_reply",
                                                           "button_reply": {"id": body.reply_id, "title": body.reply_title or body.reply_id}}}
    elif body.text:
        message = {"type": "text", "text": {"body": body.text}}
    else:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Send text or a reply")
    return _run_sim(db, _sim_payload(acc, number, body.profile_name, message))


@router.post("/simulator/inbound-media", response_model=MessageOut)
async def simulate_inbound_media(
    from_number: str = Form(...), caption: str | None = Form(default=None), profile_name: str | None = Form(default=None),
    file: UploadFile = File(...), db: Session = Depends(get_db), user: User = Depends(managers),
):
    """Pretend a buyer sent a photo, voice note or document."""
    from app.core.validators import normalize_phone

    acc = _sim_guard(db, user)
    number = normalize_phone(from_number)
    content = await file.read()
    mime = file.content_type or "application/octet-stream"
    kind = "image" if mime.startswith("image/") else "audio" if mime.startswith("audio/") else "document"
    stash = media.save(user.tenant_id, content, mime, file.filename)
    part = {"id": f"sim:{stash}|{mime}", "mime_type": mime}
    if kind == "audio":
        part["voice"] = True
    if kind == "document":
        part["filename"] = file.filename
    if caption and kind != "audio":
        part["caption"] = caption
    return _run_sim(db, _sim_payload(acc, number, profile_name, {"type": kind, kind: part}))


@router.post("/simulator/status")
def simulate_status(body: SimStatus, db: Session = Depends(get_db), user: User = Depends(managers)):
    acc = _sim_guard(db, user)
    msg = db.scalar(select(Message).where(Message.id == body.message_id, Message.tenant_id == user.tenant_id))
    if msg is None or not msg.wa_message_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Message not found or not sent yet")
    s = {"id": msg.wa_message_id, "status": body.status, "timestamp": str(int(datetime.now(UTC).timestamp()))}
    if body.status == "failed":
        s["errors"] = [{"code": 131026, "title": "Message undeliverable", "message": body.error or "Message undeliverable"}]
    handle_status(db, acc, s)
    db.commit()
    return {"status": msg.status.value}


@router.post("/simulator/templates/{template_id}/{decision}", response_model=TemplateOut)
def simulate_template_review(template_id: uuid.UUID, decision: str, db: Session = Depends(get_db),
                             user: User = Depends(managers)):
    _sim_guard(db, user)
    tpl = db.scalar(select(MessageTemplate).where(MessageTemplate.id == template_id, MessageTemplate.tenant_id == user.tenant_id))
    if tpl is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Template not found")
    if decision not in ("approve", "reject"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Use approve or reject")
    tpl.status = TemplateStatus.approved if decision == "approve" else TemplateStatus.rejected
    tpl.rejection_reason = None if decision == "approve" else "Rejected in simulator"
    db.commit()
    return tpl
