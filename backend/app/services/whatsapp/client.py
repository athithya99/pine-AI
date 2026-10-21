"""WhatsApp transport. CloudClient talks to Meta's Cloud API; SimulatorClient keeps everything local.

Both expose the same methods so the rest of the app never checks which one it has.
"""

import uuid
from typing import Any, Protocol

import httpx

from app.core.config import settings
from app.core.crypto import decrypt
from app.models import MessageTemplate, WaMode, WhatsAppAccount
from app.services import media


class WhatsAppError(Exception):
    def __init__(self, message: str, code: str | None = None):
        super().__init__(message)
        self.code = code
        self.message = message


class WaClient(Protocol):
    def send(self, to: str, message: dict[str, Any]) -> str: ...
    def mark_read(self, wa_message_id: str) -> None: ...
    def download_media(self, media_id: str) -> tuple[bytes, str]: ...
    def upload_media(self, content: bytes, mime: str, filename: str) -> str: ...
    def submit_template(self, tpl: MessageTemplate) -> tuple[str | None, str]: ...
    def fetch_templates(self) -> list[dict]: ...
    def subscribe_app(self) -> bool: ...
    def subscribed_apps(self) -> list[dict]: ...


def wa_number(e164: str) -> str:
    """Meta uses digits only: +919876543210 -> 919876543210."""
    return e164.lstrip("+")


def template_components(tpl: MessageTemplate) -> list[dict]:
    comps: list[dict] = []
    if tpl.header_format in ("IMAGE", "DOCUMENT"):
        raise WhatsAppError("Templates with an image or PDF header must be created in WhatsApp Manager "
                            "(Meta needs a sample file). Create it there, then press Sync here.")
    if tpl.header_text:
        comps.append({"type": "HEADER", "format": "TEXT", "text": tpl.header_text})
    body: dict[str, Any] = {"type": "BODY", "text": tpl.body}
    if tpl.variable_count:
        body["example"] = {"body_text": [tpl.example_values or [f"sample{i}" for i in range(1, tpl.variable_count + 1)]]}
    comps.append(body)
    if tpl.footer:
        comps.append({"type": "FOOTER", "text": tpl.footer})
    if tpl.buttons:
        comps.append({"type": "BUTTONS", "buttons": tpl.buttons})
    return comps


# tests swap this for httpx.MockTransport
TRANSPORT: httpx.BaseTransport | None = None


class CloudClient:
    def __init__(self, account: WhatsAppAccount, timeout: float = 20.0):
        token = decrypt(account.access_token_enc)
        if not token or not account.phone_number_id:
            raise WhatsAppError("WhatsApp number is not fully connected (missing token or phone number ID)")
        self.account = account
        self.base = f"{settings.WHATSAPP_GRAPH_URL}/{settings.WHATSAPP_API_VERSION}"
        self.http = httpx.Client(timeout=timeout, headers={"Authorization": f"Bearer {token}"}, transport=TRANSPORT)

    def _check(self, r: httpx.Response) -> dict:
        try:
            data = r.json()
        except ValueError:
            data = {}
        if r.status_code >= 400 or "error" in data:
            err = data.get("error", {})
            detail = err.get("error_data", {}).get("details") or err.get("message") or r.text[:300]
            raise WhatsAppError(detail, str(err.get("code") or r.status_code))
        return data

    def send(self, to: str, message: dict[str, Any]) -> str:
        body = {"messaging_product": "whatsapp", "recipient_type": "individual", "to": wa_number(to), **message}
        data = self._check(self.http.post(f"{self.base}/{self.account.phone_number_id}/messages", json=body))
        return data["messages"][0]["id"]

    def mark_read(self, wa_message_id: str) -> None:
        self._check(self.http.post(
            f"{self.base}/{self.account.phone_number_id}/messages",
            json={"messaging_product": "whatsapp", "status": "read", "message_id": wa_message_id},
        ))

    def download_media(self, media_id: str) -> tuple[bytes, str]:
        meta = self._check(self.http.get(f"{self.base}/{media_id}"))
        r = self.http.get(meta["url"])
        if r.status_code >= 400:
            raise WhatsAppError(f"Media download failed ({r.status_code})")
        return r.content, meta.get("mime_type", "application/octet-stream")

    def upload_media(self, content: bytes, mime: str, filename: str) -> str:
        data = self._check(self.http.post(
            f"{self.base}/{self.account.phone_number_id}/media",
            data={"messaging_product": "whatsapp", "type": mime},
            files={"file": (filename, content, mime)},
        ))
        return data["id"]

    def submit_template(self, tpl: MessageTemplate) -> tuple[str | None, str]:
        if not self.account.waba_id:
            raise WhatsAppError("WhatsApp Business Account ID is missing")
        data = self._check(self.http.post(
            f"{self.base}/{self.account.waba_id}/message_templates",
            json={"name": tpl.name, "language": tpl.language, "category": tpl.category.value,
                  "components": template_components(tpl)},
        ))
        return data.get("id"), data.get("status", "PENDING")

    def fetch_templates(self) -> list[dict]:
        if not self.account.waba_id:
            raise WhatsAppError("WhatsApp Business Account ID is missing")
        out: list[dict] = []
        url: str | None = f"{self.base}/{self.account.waba_id}/message_templates"
        params: dict | None = {"fields": "id,name,language,status,category,rejected_reason,components", "limit": 100}
        while url:
            data = self._check(self.http.get(url, params=params))
            out.extend(data.get("data", []))
            url = data.get("paging", {}).get("next")
            params = None
        return out

    def subscribe_app(self) -> bool:
        """Link this app to the WhatsApp Business Account so Meta sends message webhooks for it.

        Without this, the webhook URL verifies fine but no messages are ever delivered.
        Calling it again is harmless.
        """
        if not self.account.waba_id:
            raise WhatsAppError("WhatsApp Business Account ID is missing")
        data = self._check(self.http.post(f"{self.base}/{self.account.waba_id}/subscribed_apps"))
        return bool(data.get("success", True))

    def subscribed_apps(self) -> list[dict]:
        if not self.account.waba_id:
            raise WhatsAppError("WhatsApp Business Account ID is missing")
        data = self._check(self.http.get(f"{self.base}/{self.account.waba_id}/subscribed_apps"))
        return [d.get("whatsapp_business_api_data", d) for d in data.get("data", [])]


class SimulatorClient:
    """Pretends to be Meta. Outbound messages are 'sent' instantly; templates wait for manual approval."""

    def __init__(self, account: WhatsAppAccount):
        self.account = account

    def send(self, to: str, message: dict[str, Any]) -> str:
        if not to:
            raise WhatsAppError("Recipient missing")
        return f"wamid.SIM{uuid.uuid4().hex}"

    def mark_read(self, wa_message_id: str) -> None:
        return None

    def download_media(self, media_id: str) -> tuple[bytes, str]:
        # simulator media ids look like "sim:<relative path>|<mime>"
        if not media_id.startswith("sim:"):
            raise WhatsAppError("Unknown simulator media")
        path, _, mime = media_id[4:].partition("|")
        return media.read(path), mime or "application/octet-stream"

    def upload_media(self, content: bytes, mime: str, filename: str) -> str:
        return f"sim-upload-{uuid.uuid4().hex[:12]}"

    def submit_template(self, tpl: MessageTemplate) -> tuple[str | None, str]:
        return f"sim-{uuid.uuid4().hex[:10]}", "PENDING"

    def fetch_templates(self) -> list[dict]:
        return []

    def subscribe_app(self) -> bool:
        return True

    def subscribed_apps(self) -> list[dict]:
        return [{"name": "Simulator"}]


def get_client(account: WhatsAppAccount) -> WaClient:
    if account.mode == WaMode.cloud:
        return CloudClient(account)
    return SimulatorClient(account)
