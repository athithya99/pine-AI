"""Payment gateways behind one interface (FR-M8-03). Links are created on the seller's own account.

- simulator: no gateway account; the link opens a test page in this app where the payment can be marked paid
- razorpay: Razorpay Payment Links API (test or live keys); money settles to the seller's Razorpay account
"""

import hashlib
import hmac
import time
import uuid
from dataclasses import dataclass
from datetime import datetime

import httpx

from app.core.config import settings
from app.core.crypto import decrypt
from app.models import GatewayMode, PaymentGatewayAccount

# tests swap this for httpx.MockTransport
TRANSPORT: httpx.BaseTransport | None = None


class GatewayError(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


@dataclass
class CreatedLink:
    gateway_link_id: str
    short_url: str


class SimulatorGateway:
    mode = GatewayMode.simulator

    def create_link(self, link_id: uuid.UUID, amount_paise: int, description: str, customer_name: str,
                    customer_phone: str, expires_at: datetime | None) -> CreatedLink:
        return CreatedLink(f"plink_sim_{uuid.uuid4().hex[:14]}", f"{settings.PUBLIC_APP_URL.rstrip('/')}/pay/{link_id}")

    def cancel_link(self, gateway_link_id: str) -> None:
        return None

    def verify_webhook(self, raw: bytes, signature: str | None) -> bool:
        return False  # the simulator never receives webhooks


class RazorpayGateway:
    mode = GatewayMode.razorpay

    def __init__(self, account: PaymentGatewayAccount):
        self.key_id = account.key_id or ""
        self.secret = decrypt(account.key_secret_enc) or ""
        self.webhook_secret = decrypt(account.webhook_secret_enc) or ""
        if not self.key_id or not self.secret:
            raise GatewayError("Razorpay is not fully connected (missing Key ID or Key Secret)")

    def _client(self) -> httpx.Client:
        return httpx.Client(base_url=settings.RAZORPAY_API_URL, auth=(self.key_id, self.secret), timeout=20,
                            transport=TRANSPORT)

    @staticmethod
    def _check(r: httpx.Response) -> dict:
        try:
            data = r.json()
        except ValueError:
            data = {}
        if r.status_code >= 400:
            err = data.get("error", {}) if isinstance(data, dict) else {}
            raise GatewayError(f"Razorpay: {err.get('description') or r.text[:200]}")
        return data

    def create_link(self, link_id, amount_paise, description, customer_name, customer_phone, expires_at) -> CreatedLink:
        body = {
            "amount": amount_paise, "currency": "INR", "accept_partial": False, "description": description[:2048],
            "reference_id": str(link_id)[:40],
            "customer": {"name": customer_name[:100], "contact": customer_phone},
            "notify": {"sms": False, "email": False},  # we send it on WhatsApp ourselves
            "reminder_enable": False,
            "notes": {"link_id": str(link_id)},
        }
        if expires_at:
            body["expire_by"] = int(expires_at.timestamp())
        try:
            with self._client() as c:
                data = self._check(c.post("/payment_links", json=body))
        except httpx.HTTPError as exc:
            raise GatewayError(f"Could not reach Razorpay: {exc}") from exc
        return CreatedLink(data["id"], data["short_url"])

    def cancel_link(self, gateway_link_id: str) -> None:
        try:
            with self._client() as c:
                self._check(c.post(f"/payment_links/{gateway_link_id}/cancel"))
        except httpx.HTTPError as exc:
            raise GatewayError(f"Could not reach Razorpay: {exc}") from exc

    def verify_webhook(self, raw: bytes, signature: str | None) -> bool:
        if not self.webhook_secret or not signature:
            return False
        expected = hmac.new(self.webhook_secret.encode(), raw, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signature)

    def test(self) -> None:
        """Cheap authenticated call to check the keys."""
        try:
            with self._client() as c:
                self._check(c.get("/payment_links", params={"count": 1}))
        except httpx.HTTPError as exc:
            raise GatewayError(f"Could not reach Razorpay: {exc}") from exc


def get_gateway(account: PaymentGatewayAccount | None):
    if account is None or account.mode == GatewayMode.simulator:
        return SimulatorGateway()
    return RazorpayGateway(account)


def expiry(days: int = 7) -> datetime:
    from datetime import UTC, timedelta

    return datetime.fromtimestamp(time.time(), UTC) + timedelta(days=days)
