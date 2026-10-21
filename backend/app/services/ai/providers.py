"""AI providers behind one interface. Switch with AI_PROVIDER in .env; nothing else changes.

- mock: built-in rule parser (no key, typed orders only) for development and tests
- anthropic: Claude via the Messages API (forced tool call returns our JSON)
- openai: GPT via Chat Completions with a JSON schema response format
Speech-to-text for voice notes: STT_PROVIDER=openai (audio/transcriptions).
"""

import base64
import json
import time
from dataclasses import dataclass, field
from decimal import Decimal

import httpx
from pydantic import ValidationError

from app.core.config import settings
from app.services.ai import mock_parser
from app.services.ai.schema import JSON_SCHEMA, SYSTEM_PROMPT, Extraction

# tests swap this for httpx.MockTransport
TRANSPORT: httpx.BaseTransport | None = None

DEFAULT_MODELS = {
    "anthropic": ("claude-haiku-5-5", "claude-sonnet-5-5"),
    "openai": ("gpt-5.4-mini", "gpt-5.4"),
    "mock": ("rules-v1", "rules-v1"),
}
# USD per million tokens (input, output) - list prices, Oct 2026. Used for the cost column only.
PRICES = {
    "claude-haiku-5-5": (Decimal("0.10"), Decimal("0.50")),
    "claude-sonnet-5-5": (Decimal("2"), Decimal("10")),
    "claude-opus-5-5": (Decimal("4"), Decimal("20")),
    "gpt-5.4-mini": (Decimal("0.75"), Decimal("4.50")),
    "gpt-5.4-nano": (Decimal("0.20"), Decimal("1.25")),
    "gpt-5.4": (Decimal("2.50"), Decimal("15")),
    "gpt-5.5": (Decimal("5"), Decimal("30")),
}
MAX_IMAGE_BYTES = 5 * 1024 * 1024
IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}


class AIError(Exception):
    """AI call failed or timed out. The message goes to staff and the buyer gets an acknowledgement."""


@dataclass
class AIResult:
    extraction: Extraction
    provider: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: int = 0
    raw: dict = field(default_factory=dict)

    @property
    def cost_usd(self) -> Decimal | None:
        p = PRICES.get(self.model)
        if p is None:
            return Decimal(0) if self.provider == "mock" else None
        return (p[0] * self.input_tokens + p[1] * self.output_tokens) / Decimal(1_000_000)


@dataclass
class JsonResult:
    """Free-form structured answer (e.g. fields read from an invoice PDF)."""
    data: dict
    provider: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: int = 0

    @property
    def cost_usd(self) -> Decimal | None:
        p = PRICES.get(self.model)
        if p is None:
            return None
        return (p[0] * self.input_tokens + p[1] * self.output_tokens) / Decimal(1_000_000)


@dataclass
class Transcript:
    text: str
    provider: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: int = 0


def _client(headers: dict) -> httpx.Client:
    return httpx.Client(timeout=settings.AI_TIMEOUT_SECONDS, headers=headers, transport=TRANSPORT)


def _parse_extraction(data: dict) -> Extraction:
    try:
        return Extraction.model_validate(data)
    except ValidationError as exc:
        raise AIError(f"AI returned data in the wrong shape: {exc.errors()[0].get('msg')}") from exc


class Provider:
    name = "base"
    vision = False

    def __init__(self) -> None:
        fast, smart = DEFAULT_MODELS.get(self.name, ("", ""))
        self.fast = settings.AI_MODEL_FAST or fast
        self.smart = settings.AI_MODEL_SMART or smart

    def extract(self, text: str | None, context: str, *, image: tuple[bytes, str] | None = None,
                has_open_quote: bool = False) -> AIResult:
        raise NotImplementedError


class MockProvider(Provider):
    name = "mock"

    def extract(self, text, context, *, image=None, has_open_quote=False) -> AIResult:
        if image is not None:
            raise AIError("The development AI (mock) can't read photos. Set AI_PROVIDER to anthropic or openai.")
        start = time.monotonic()
        ex = mock_parser.parse(text, has_open_quote)
        return AIResult(ex, self.name, self.fast, latency_ms=int((time.monotonic() - start) * 1000),
                        raw=ex.model_dump())


def _user_text(text: str | None, context: str) -> str:
    msg = text.strip() if text and text.strip() else "(no text - read the attached image)"
    return f"{context}\n\nBuyer's message:\n{msg}"


class AnthropicProvider(Provider):
    name = "anthropic"
    vision = True

    def extract(self, text, context, *, image=None, has_open_quote=False) -> AIResult:
        if not settings.AI_API_KEY:
            raise AIError("AI_API_KEY is not set")
        model = self.smart if image else self.fast
        content: list[dict] = []
        if image:
            data, mime = image
            content.append({"type": "image", "source": {"type": "base64", "media_type": mime,
                                                        "data": base64.b64encode(data).decode()}})
        content.append({"type": "text", "text": _user_text(text, context)})
        body = {
            "model": model, "max_tokens": 2000, "system": SYSTEM_PROMPT,
            "messages": [{"role": "user", "content": content}],
            "tools": [{"name": "record_message", "description": "Record what the buyer's message says",
                       "input_schema": JSON_SCHEMA}],
            "tool_choice": {"type": "tool", "name": "record_message"},
        }
        start = time.monotonic()
        try:
            with _client({"x-api-key": settings.AI_API_KEY, "anthropic-version": "2023-06-01"}) as c:
                r = c.post(f"{settings.ANTHROPIC_API_URL}/messages", json=body)
        except httpx.HTTPError as exc:
            raise AIError(f"Could not reach Anthropic: {exc}") from exc
        latency = int((time.monotonic() - start) * 1000)
        try:
            data = r.json()
        except ValueError:
            data = {}
        if r.status_code >= 400:
            raise AIError(f"Anthropic error {r.status_code}: {data.get('error', {}).get('message') or r.text[:200]}")
        block = next((b for b in data.get("content", []) if b.get("type") == "tool_use"), None)
        if block is None:
            raise AIError("Anthropic returned no structured answer")
        usage = data.get("usage", {})
        return AIResult(_parse_extraction(block.get("input") or {}), self.name, data.get("model") or model,
                        usage.get("input_tokens", 0), usage.get("output_tokens", 0), latency, raw=block.get("input"))


    def extract_json(self, system: str, text: str, schema: dict, *, pdf: bytes | None = None,
                     image: tuple[bytes, str] | None = None, fast: bool = False) -> JsonResult:
        if not settings.AI_API_KEY:
            raise AIError("AI_API_KEY is not set")
        model = self.fast if fast and not (pdf or image) else self.smart
        content: list[dict] = []
        if pdf:
            content.append({"type": "document", "source": {"type": "base64", "media_type": "application/pdf",
                                                           "data": base64.b64encode(pdf).decode()}})
        if image:
            content.append({"type": "image", "source": {"type": "base64", "media_type": image[1],
                                                        "data": base64.b64encode(image[0]).decode()}})
        content.append({"type": "text", "text": text})
        body = {"model": model, "max_tokens": 1500, "system": system, "messages": [{"role": "user", "content": content}],
                "tools": [{"name": "record", "description": "Record the extracted fields", "input_schema": schema}],
                "tool_choice": {"type": "tool", "name": "record"}}
        start = time.monotonic()
        try:
            with _client({"x-api-key": settings.AI_API_KEY, "anthropic-version": "2023-06-01"}) as c:
                r = c.post(f"{settings.ANTHROPIC_API_URL}/messages", json=body)
        except httpx.HTTPError as exc:
            raise AIError(f"Could not reach Anthropic: {exc}") from exc
        latency = int((time.monotonic() - start) * 1000)
        try:
            data = r.json()
        except ValueError:
            data = {}
        if r.status_code >= 400:
            raise AIError(f"Anthropic error {r.status_code}: {data.get('error', {}).get('message') or r.text[:200]}")
        block = next((b for b in data.get("content", []) if b.get("type") == "tool_use"), None)
        if block is None:
            raise AIError("Anthropic returned no structured answer")
        usage = data.get("usage", {})
        return JsonResult(block.get("input") or {}, self.name, data.get("model") or model,
                          usage.get("input_tokens", 0), usage.get("output_tokens", 0), latency)


class OpenAIProvider(Provider):
    name = "openai"
    vision = True

    def extract(self, text, context, *, image=None, has_open_quote=False) -> AIResult:
        if not settings.AI_API_KEY:
            raise AIError("AI_API_KEY is not set")
        model = self.smart if image else self.fast
        content: list[dict] = [{"type": "text", "text": _user_text(text, context)}]
        if image:
            data, mime = image
            content.append({"type": "image_url",
                            "image_url": {"url": f"data:{mime};base64,{base64.b64encode(data).decode()}"}})
        body = {
            "model": model, "max_completion_tokens": 2000,
            "messages": [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": content}],
            "response_format": {"type": "json_schema",
                                "json_schema": {"name": "buyer_message", "schema": JSON_SCHEMA, "strict": False}},
        }
        start = time.monotonic()
        try:
            with _client({"Authorization": f"Bearer {settings.AI_API_KEY}"}) as c:
                r = c.post(f"{settings.OPENAI_API_URL}/chat/completions", json=body)
        except httpx.HTTPError as exc:
            raise AIError(f"Could not reach OpenAI: {exc}") from exc
        latency = int((time.monotonic() - start) * 1000)
        try:
            data = r.json()
        except ValueError:
            data = {}
        if r.status_code >= 400:
            raise AIError(f"OpenAI error {r.status_code}: {data.get('error', {}).get('message') or r.text[:200]}")
        try:
            raw = json.loads(data["choices"][0]["message"]["content"])
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise AIError("OpenAI returned no structured answer") from exc
        usage = data.get("usage", {})
        return AIResult(_parse_extraction(raw), self.name, data.get("model") or model,
                        usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0), latency, raw=raw)


    def extract_json(self, system: str, text: str, schema: dict, *, pdf: bytes | None = None,
                     image: tuple[bytes, str] | None = None, fast: bool = False) -> JsonResult:
        if not settings.AI_API_KEY:
            raise AIError("AI_API_KEY is not set")
        model = self.fast if fast and not (pdf or image) else self.smart
        content: list[dict] = [{"type": "text", "text": text}]
        if image:
            content.append({"type": "image_url",
                            "image_url": {"url": f"data:{image[1]};base64,{base64.b64encode(image[0]).decode()}"}})
        if pdf:
            content.append({"type": "file", "file": {
                "filename": "invoice.pdf", "file_data": f"data:application/pdf;base64,{base64.b64encode(pdf).decode()}"}})
        body = {"model": model, "max_completion_tokens": 1500,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": content}],
                "response_format": {"type": "json_schema", "json_schema": {"name": "fields", "schema": schema,
                                                                           "strict": False}}}
        start = time.monotonic()
        try:
            with _client({"Authorization": f"Bearer {settings.AI_API_KEY}"}) as c:
                r = c.post(f"{settings.OPENAI_API_URL}/chat/completions", json=body)
        except httpx.HTTPError as exc:
            raise AIError(f"Could not reach OpenAI: {exc}") from exc
        latency = int((time.monotonic() - start) * 1000)
        try:
            data = r.json()
        except ValueError:
            data = {}
        if r.status_code >= 400:
            raise AIError(f"OpenAI error {r.status_code}: {data.get('error', {}).get('message') or r.text[:200]}")
        try:
            raw = json.loads(data["choices"][0]["message"]["content"])
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise AIError("OpenAI returned no structured answer") from exc
        usage = data.get("usage", {})
        return JsonResult(raw, self.name, data.get("model") or model, usage.get("prompt_tokens", 0),
                          usage.get("completion_tokens", 0), latency)


PROVIDERS = {"mock": MockProvider, "anthropic": AnthropicProvider, "openai": OpenAIProvider}


def get_provider() -> Provider:
    cls = PROVIDERS.get((settings.AI_PROVIDER or "mock").lower())
    if cls is None:
        raise AIError(f"Unknown AI_PROVIDER '{settings.AI_PROVIDER}'")
    return cls()


# ---------------- speech to text ----------------
def stt_configured() -> bool:
    return settings.STT_PROVIDER.lower() == "openai" and bool(_stt_key())


def _stt_key() -> str:
    return settings.STT_API_KEY or (settings.AI_API_KEY if settings.AI_PROVIDER.lower() == "openai" else "")


def transcribe(audio: bytes, mime: str, filename: str | None = None) -> Transcript:
    if not stt_configured():
        raise AIError("Voice notes need speech-to-text. Set STT_PROVIDER=openai and an API key.")
    ext = {"audio/ogg": "ogg", "audio/mpeg": "mp3", "audio/mp4": "m4a", "audio/aac": "aac", "audio/amr": "amr",
           "audio/wav": "wav", "audio/webm": "webm"}.get(mime.split(";")[0], "ogg")
    start = time.monotonic()
    try:
        with _client({"Authorization": f"Bearer {_stt_key()}"}) as c:
            r = c.post(f"{settings.OPENAI_API_URL}/audio/transcriptions",
                       data={"model": settings.STT_MODEL, "response_format": "json",
                             "prompt": "A shop owner ordering goods in Tamil, Hindi or English."},
                       files={"file": (filename or f"voice.{ext}", audio, mime.split(";")[0])})
    except httpx.HTTPError as exc:
        raise AIError(f"Could not reach the speech-to-text service: {exc}") from exc
    latency = int((time.monotonic() - start) * 1000)
    try:
        data = r.json()
    except ValueError:
        data = {}
    if r.status_code >= 400 or "text" not in data:
        raise AIError(f"Speech-to-text error {r.status_code}: {data.get('error', {}).get('message') or r.text[:200]}")
    usage = data.get("usage") or {}
    return Transcript(data["text"], "openai", settings.STT_MODEL, usage.get("input_tokens", 0),
                      usage.get("output_tokens", 0), latency)
