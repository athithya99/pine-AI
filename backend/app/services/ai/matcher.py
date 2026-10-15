"""Match the buyer's item text to catalog products with a confidence score (FR-M5-05).

Uses SKU, name and aliases. Scores near 1.0 mean an exact SKU/name/alias match; a word-overlap match where only
one product fits scores ~0.8-0.95; when two products fit almost equally the score is lowered so the line is sent
for clarification instead of guessing. Semantic (embedding) search is a Phase 2 upgrade.
"""

import re
from dataclasses import dataclass
from difflib import SequenceMatcher

from app.models import Product

AMBIGUITY_GAP = 0.05
AMBIGUITY_PENALTY = 0.15


def norm(s: str | None) -> str:
    s = (s or "").lower()
    s = re.sub(r"[^\w\s]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _tokens(s: str) -> list[str]:
    out = []
    for t in norm(s).split():
        if len(t) > 3 and t.endswith("s") and not t.endswith("ss"):
            t = t[:-1]  # bags -> bag
        out.append(t)
    return out


def _tok_match(a: str, b: str) -> bool:
    if a == b:
        return True
    if len(a) >= 4 and len(b) >= 4:
        return SequenceMatcher(None, a, b).ratio() >= 0.8
    return False


@dataclass
class Match:
    product: Product
    score: float


def score(text: str, product: Product) -> float:
    t = norm(text)
    if not t:
        return 0.0
    if t == norm(product.sku):
        return 1.0
    names = [product.name, *(product.aliases or [])]
    if any(t == norm(n) for n in names):
        return 0.98
    best = 0.0
    tt = _tokens(t)
    for n in names:
        nt = _tokens(n)
        if not nt or not tt:
            continue
        text_cov = sum(1 for a in tt if any(_tok_match(a, b) for b in nt)) / len(tt)
        name_cov = sum(1 for b in nt if any(_tok_match(a, b) for a in tt)) / len(nt)
        if text_cov == 1:
            s = 0.75 + 0.2 * name_cov
        else:
            s = 0.55 * text_cov + 0.2 * name_cov
        s = max(s, SequenceMatcher(None, t, norm(n)).ratio() * 0.9)
        best = max(best, s)
    return round(min(best, 0.97), 3)


def rank(text: str, products: list[Product], limit: int = 3) -> list[Match]:
    scored = [Match(p, score(text, p)) for p in products]
    scored = [m for m in scored if m.score > 0.3]
    scored.sort(key=lambda m: m.score, reverse=True)
    return scored[:limit]


def best_match(text: str, products: list[Product], sku_hint: str | None = None,
               hint_confidence: float = 0) -> tuple[Product | None, float, list[Match]]:
    """Returns (product, confidence, top candidates)."""
    ranked = rank(text, products)
    if sku_hint:
        hinted = next((p for p in products if p.sku.lower() == sku_hint.strip().lower()), None)
        if hinted is not None:
            own = score(text, hinted)
            conf = max(own, min(hint_confidence, 0.99))
            others = [m for m in ranked if m.product.id != hinted.id]
            return hinted, round(conf, 3), [Match(hinted, conf), *others][:3]
    if not ranked:
        return None, 0.0, []
    top = ranked[0]
    conf = top.score
    if len(ranked) > 1 and ranked[1].score >= top.score - AMBIGUITY_GAP and top.score < 0.98:
        conf = max(0.0, conf - AMBIGUITY_PENALTY)
    return top.product, round(conf, 3), ranked
