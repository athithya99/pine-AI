"""Public webhook endpoints. No login; requests are verified by signature instead (NFR-03)."""

import hashlib
import hmac
import json
import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.crypto import decrypt
from app.core.database import get_db
from app.models import WebhookEvent, WhatsAppAccount
from app.worker.tasks import process_webhook_event

router = APIRouter(prefix="/api/webhooks", tags=["Webhooks"])
log = logging.getLogger("pinesphere.webhooks")


@router.get("/whatsapp")
def verify_whatsapp(
    mode: str = Query(alias="hub.mode"),
    token: str = Query(alias="hub.verify_token"),
    challenge: str = Query(alias="hub.challenge"),
):
    """Meta calls this once when you save the webhook URL in the app dashboard."""
    if mode == "subscribe" and hmac.compare_digest(token, settings.WHATSAPP_VERIFY_TOKEN):
        return Response(content=challenge, media_type="text/plain")
    raise HTTPException(status.HTTP_403_FORBIDDEN, "Verification failed")


def _phone_number_ids(payload: dict) -> set[str]:
    ids = set()
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            pnid = change.get("value", {}).get("metadata", {}).get("phone_number_id")
            if pnid:
                ids.add(pnid)
    return ids


def signature_valid(db: Session, raw: bytes, header: str | None, payload: dict) -> bool:
    if not header or not header.startswith("sha256="):
        return False
    secrets = [settings.WHATSAPP_APP_SECRET] if settings.WHATSAPP_APP_SECRET else []
    for acc in db.scalars(select(WhatsAppAccount).where(WhatsAppAccount.phone_number_id.in_(_phone_number_ids(payload)))):
        s = decrypt(acc.app_secret_enc)
        if s:
            secrets.append(s)
    given = header.removeprefix("sha256=")
    return any(hmac.compare_digest(hmac.new(s.encode(), raw, hashlib.sha256).hexdigest(), given) for s in secrets)


@router.post("/whatsapp")
async def receive_whatsapp(request: Request, db: Session = Depends(get_db)):
    raw = await request.body()
    try:
        payload = json.loads(raw)
    except ValueError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid JSON") from None
    if not signature_valid(db, raw, request.headers.get("X-Hub-Signature-256"), payload):
        log.warning("Rejected WhatsApp webhook with missing or invalid signature")
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid signature")
    event = WebhookEvent(source="whatsapp", payload=payload)
    db.add(event)
    db.commit()
    # acknowledge fast; processing happens in the background (NFR-07)
    process_webhook_event.delay(str(event.id))
    return {"status": "ok"}


@router.post("/razorpay/{tenant_id}")
async def receive_razorpay(tenant_id: uuid.UUID, request: Request, db: Session = Depends(get_db)):
    """Razorpay calls this when a payment link is paid. Set this URL + secret in Razorpay → Webhooks."""
    from app.models import PaymentGatewayAccount, PaymentLink
    from app.services import orders as order_svc
    from app.services import payments as pay_svc
    from app.services.gateways import GatewayError, RazorpayGateway

    raw = await request.body()
    account = db.scalar(select(PaymentGatewayAccount).where(PaymentGatewayAccount.tenant_id == tenant_id))
    try:
        gateway = RazorpayGateway(account) if account else None
    except GatewayError:
        gateway = None
    if gateway is None or not gateway.verify_webhook(raw, request.headers.get("X-Razorpay-Signature")):
        log.warning("Rejected Razorpay webhook for tenant %s: bad signature", tenant_id)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid signature")
    try:
        payload = json.loads(raw)
    except ValueError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid JSON") from None
    db.add(WebhookEvent(source="razorpay", payload=payload, processed=True))
    if payload.get("event") == "payment_link.paid":
        plink = payload.get("payload", {}).get("payment_link", {}).get("entity", {})
        pay = payload.get("payload", {}).get("payment", {}).get("entity", {})
        link = db.scalar(select(PaymentLink).where(PaymentLink.tenant_id == tenant_id,
                                                   PaymentLink.gateway_link_id == plink.get("id")).with_for_update())
        if link is not None:
            from decimal import Decimal

            amount = Decimal(pay.get("amount", 0)) / 100 if pay.get("amount") else None
            pay_svc.link_paid(db, link, gateway_payment_id=pay.get("id"), amount=amount, method=pay.get("method"))
        else:
            log.warning("Razorpay webhook for unknown link %s", plink.get("id"))
    order_svc.commit_and_send(db)
    return {"status": "ok"}
