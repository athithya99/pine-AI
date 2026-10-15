"""Public webhook endpoints. No login; requests are verified by signature instead (NFR-03)."""

import hashlib
import hmac
import json
import logging

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
