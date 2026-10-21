import logging
import uuid

from app.core.database import SessionLocal
from app.models import Message, SenderKind, WebhookEvent
from app.worker.celery_app import celery_app

log = logging.getLogger("pinesphere.tasks")

RETRY_DELAYS = [0, 10, 60]  # seconds before attempt 1, 2, 3


@celery_app.task(name="whatsapp.process_webhook")
def process_webhook_event(event_id: str) -> None:
    from app.services.whatsapp.inbound import process_payload

    with SessionLocal() as db:
        event = db.get(WebhookEvent, uuid.UUID(event_id))
        if event is None or event.processed:
            return
        try:
            created = process_payload(db, event.payload)
            event.processed = True
            db.commit()
        except Exception as exc:  # keep the raw event for re-processing
            db.rollback()
            event = db.get(WebhookEvent, uuid.UUID(event_id))
            event.error = str(exc)[:2000]
            db.commit()
            log.exception("Webhook %s failed", event_id)
            return
        for msg in created:
            after_inbound(msg)


def after_inbound(msg: Message) -> None:
    """Follow-up work for a new inbound message."""
    if (msg.payload or {}).get("opt_out"):
        confirm_opt_out.delay(str(msg.id))
        return
    if (msg.payload or {}).get("media_id"):
        fetch_inbound_media.delay(str(msg.id))  # hands over to the bot once the file is saved
    else:
        handle_buyer_message.delay(str(msg.id))


@celery_app.task(name="whatsapp.fetch_media")
def fetch_inbound_media(message_id: str) -> None:
    """Meta media links expire, so copy the file to our storage on arrival (FR-M4-03)."""
    from sqlalchemy import select

    from app.models import WhatsAppAccount
    from app.services import media
    from app.services.whatsapp.client import get_client

    with SessionLocal() as db:
        msg = db.get(Message, uuid.UUID(message_id))
        if msg is None or msg.media_path:
            return
        account = db.scalar(select(WhatsAppAccount).where(WhatsAppAccount.tenant_id == msg.tenant_id))
        try:
            content, mime = get_client(account).download_media(msg.payload["media_id"])
            msg.media_path = media.save(msg.tenant_id, content, mime, msg.media_filename)
            msg.media_mime = mime
        except Exception as exc:
            log.warning("Media download failed for %s: %s", message_id, exc)
            msg.error_message = f"Media download failed: {exc}"[:500]
        db.commit()
    handle_buyer_message.delay(message_id)


@celery_app.task(name="whatsapp.confirm_opt_out")
def confirm_opt_out(message_id: str) -> None:
    from app.models import Customer
    from app.services.whatsapp.outbound import MessageType, SenderKind, _create, get_or_create_conversation

    with SessionLocal() as db:
        msg = db.get(Message, uuid.UUID(message_id))
        customer = db.get(Customer, msg.customer_id)
        conv = get_or_create_conversation(db, customer)
        text = "You have been unsubscribed and won't receive further messages from us. Reply START to subscribe again."
        out = _create(db, customer, conv, msg_type=MessageType.text, text=text,
                      payload={"type": "text", "text": {"body": text}}, sender=SenderKind.system, user=None)
        db.commit()
        send_message.delay(str(out.id))


@celery_app.task(name="whatsapp.send")
def send_message(message_id: str, attempt: int = 1) -> None:
    from app.services.whatsapp.outbound import deliver

    with SessionLocal() as db:
        retry = deliver(db, uuid.UUID(message_id), attempt)
    if retry:
        send_message.apply_async((message_id, attempt + 1), countdown=RETRY_DELAYS[min(attempt, len(RETRY_DELAYS) - 1)])


@celery_app.task(name="whatsapp.mark_read")
def mark_read(message_id: str) -> None:
    from sqlalchemy import select

    from app.models import WhatsAppAccount
    from app.services.whatsapp.client import get_client

    with SessionLocal() as db:
        msg = db.get(Message, uuid.UUID(message_id))
        if msg is None or not msg.wa_message_id:
            return
        account = db.scalar(select(WhatsAppAccount).where(WhatsAppAccount.tenant_id == msg.tenant_id))
        try:
            get_client(account).mark_read(msg.wa_message_id)
        except Exception as exc:
            log.info("mark_read failed: %s", exc)


@celery_app.task(name="bot.buyer_message")
def handle_buyer_message(message_id: str) -> None:
    """Quote buttons (Confirm / Edit / Cancel, M6) and AI handling of everything else (M5)."""
    from sqlalchemy import select

    from app.models import Customer, Direction, Order
    from app.services import campaigns
    from app.services import orders as svc
    from app.services.ai import assistant
    from app.services.whatsapp import outbound

    with SessionLocal() as db:
        msg = db.get(Message, uuid.UUID(message_id))
        if msg is None or msg.direction != Direction.inbound:
            return
        customer = db.get(Customer, msg.customer_id)
        reply_id = (msg.payload or {}).get("reply_id")
        campaign_id = campaigns.parse_tap(reply_id)
        if campaign_id is not None:  # "Order now" on a campaign message (M9)
            campaigns.handle_tap(db, msg, campaign_id)
            svc.commit_and_send(db)
            return
        button = svc.parse_button(reply_id)
        if button is None:
            try:
                assistant.process_message(db, msg)
                svc.commit_and_send(db)
            except Exception:  # never lose the message: staff see it in the inbox, buyer gets an acknowledgement
                log.exception("AI processing failed for message %s", message_id)
                db.rollback()
                msg = db.get(Message, uuid.UUID(message_id))
                bot = assistant.Assistant(db, msg)
                bot._log("extract", "error", "-", input_text=msg.text, error="Unexpected error while processing")
                bot.acknowledge()
                svc.commit_and_send(db)
            return

        action, order_id = button
        reply: str | None = None
        order = db.scalar(select(Order).where(Order.id == order_id, Order.tenant_id == msg.tenant_id,
                                              Order.customer_id == customer.id).with_for_update())
        if order is None:
            reply = "Sorry, we couldn't find that order. Our team will get back to you."
        else:
            try:
                if action == "confirm":
                    svc.confirm(db, order, by_buyer=True)
                elif action == "cancel":
                    svc.cancel(db, order, "Cancelled by buyer on WhatsApp", by_buyer=True)
                else:
                    reply = (f"Sure. Tell us what you'd like to change in order {order.number}, e.g. "
                             "\"make item 1 20\" or \"remove item 2\", and we'll send a revised quote.")
            except svc.OrderError as exc:
                if exc.code == "quote_expired":
                    svc.commit_and_send(db)  # buyer already gets the "quote expired" message
                    return
                db.rollback()
                reply = exc.message
        if reply:
            try:
                out = outbound.queue_text(db, customer, reply, sender=SenderKind.bot)
                svc._pending(db).append(out.id)
            except outbound.SendBlocked as exc:
                log.info("Order reply not sent: %s", exc.message)
        svc.commit_and_send(db)


@celery_app.task(name="orders.expire_quotes")
def expire_quotes() -> int:
    """Runs every 10 minutes (celery beat). Quotes past validity become Expired and the buyer is told."""
    from app.services import orders as svc

    with SessionLocal() as db:
        n = svc.expire_due_quotes(db)
        svc.commit_and_send(db)
    return n


@celery_app.task(name="collections.reminders")
def send_payment_reminders() -> dict:
    """Daily (celery beat): payment reminders for every active company (FR-M8-01)."""
    from sqlalchemy import select

    from app.models import Tenant, TenantStatus
    from app.services import orders as svc
    from app.services.reminders import run_reminders

    totals = {"customers": 0, "sent": 0, "skipped": 0}
    with SessionLocal() as db:
        for tenant in db.scalars(select(Tenant).where(Tenant.status == TenantStatus.active)):
            try:
                r = run_reminders(db, tenant)
                svc.commit_and_send(db)
                totals = {k: totals[k] + r[k] for k in totals}
            except Exception:
                db.rollback()
                log.exception("Reminders failed for tenant %s", tenant.id)
    return totals


@celery_app.task(name="campaigns.dispatch")
def dispatch_campaigns() -> int:
    """Every minute (celery beat): start scheduled campaigns and continue ones waiting on the messaging limit."""
    from app.services import campaigns
    from app.services import orders as svc

    with SessionLocal() as db:
        try:
            n = campaigns.run_due(db)
            svc.commit_and_send(db)
        except Exception:
            db.rollback()
            log.exception("Campaign dispatch failed")
            return 0
    return n


@celery_app.task(name="assistant.daily_summaries")
def daily_summaries() -> int:
    """Every minute: the owner's daily summary once the company's set time has passed (FR-M10-08)."""
    from app.services.automation import run_due_summaries

    with SessionLocal() as db:
        return run_due_summaries(db)


@celery_app.task(name="assistant.rules")
def owner_rules() -> int:
    """Every 5 minutes: run owner rules (e.g. Monday reminders) that are due (FR-M10-09)."""
    from app.services import orders as svc
    from app.services.automation import run_due_rules

    with SessionLocal() as db:
        try:
            n = run_due_rules(db)
            svc.commit_and_send(db)
        except Exception:
            db.rollback()
            log.exception("Owner rules failed")
            return 0
    return n
