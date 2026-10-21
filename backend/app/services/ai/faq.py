"""Answer general questions from the seller's FAQ (FR-M10-03).

With a real AI provider the model picks the FAQ entry (it understands Tamil/Hindi and paraphrases); the mock provider
matches words. Either way the reply is the seller's own answer text, never AI-written.
"""

import re
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import FaqEntry
from app.services.ai.providers import AIError, JsonResult, get_provider

STOP = {
    "the", "a", "an", "is", "are", "do", "does", "you", "your", "we", "i", "me", "my", "to", "of", "for", "in", "on",
    "at", "what", "when", "where", "which", "how", "can", "will", "and", "or", "it", "be", "with", "from", "this",
    "that", "any", "have", "has", "please", "pls", "tell", "about", "there", "if", "yes", "no", "hi", "hello", "sir",
}
MIN_SCORE = 0.5


def _words(text: str) -> set[str]:
    out = set()
    for w in re.findall(r"[\w']+", (text or "").lower()):
        if w in STOP or len(w) < 3:
            continue
        out.add(w[:-1] if len(w) > 4 and w.endswith("s") else w)  # crude plural folding: areas -> area
    return out


def word_match(text: str, faqs: list[FaqEntry]) -> FaqEntry | None:
    asked = _words(text)
    if not asked:
        return None
    best, best_score = None, 0.0
    for f in faqs:
        keys = _words(f.question) | _words(" ".join(f.keywords or []))
        if not keys:
            continue
        hit = len(asked & keys)
        score = hit / min(len(asked), len(keys)) if hit else 0
        if score > best_score:
            best, best_score = f, score
    return best if best_score >= MIN_SCORE else None


SCHEMA = {"type": "object", "properties": {"faq_id": {"type": ["string", "null"]}}, "required": ["faq_id"]}
SYSTEM = ("A buyer sent this WhatsApp message to their supplier. Pick the FAQ entry that answers it, or null if none "
          "of them clearly does. Answer only with the id.")


def find(db: Session, tenant_id: uuid.UUID, text: str | None) -> tuple[FaqEntry | None, JsonResult | None]:
    faqs = list(db.scalars(select(FaqEntry).where(FaqEntry.tenant_id == tenant_id, FaqEntry.is_active.is_(True))))
    if not faqs or not (text or "").strip():
        return None, None
    provider = get_provider()
    if provider.name == "mock" or not hasattr(provider, "extract_json"):
        return word_match(text, faqs), None
    listing = "\n".join(f"{f.id} | {f.question} | {', '.join(f.keywords or [])}" for f in faqs)
    try:
        res = provider.extract_json(SYSTEM, f"FAQ (id | question | keywords):\n{listing}\n\nBuyer's message:\n{text}",
                                    SCHEMA, fast=True)
    except AIError:
        return word_match(text, faqs), None
    by_id = {str(f.id): f for f in faqs}
    return by_id.get(str(res.data.get("faq_id") or "")), res


def answer_in(f: FaqEntry, lang: str) -> str:
    return {"ta": f.answer_ta, "hi": f.answer_hi}.get(lang) or f.answer
