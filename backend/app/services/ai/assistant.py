"""AI order capture (SRS section 8, M5): buyer message -> intent -> draft order + quote, or the right reply.

Business rules:
- the AI never confirms an order; only the buyer's Confirm button or staff do (M6)
- prices always come from the pricing engine, never from AI text
- if the AI fails, the message stays in the inbox for staff and the buyer gets an acknowledgement (FR-M5-11)
- every AI call is logged with tokens, cost and latency (FR-M5-10)
"""

import logging
import re
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    AiLog,
    Conversation,
    Customer,
    Direction,
    Message,
    MessageType,
    Order,
    OrderSource,
    OrderStatus,
    Product,
    SenderKind,
    Tenant,
)
from app.services import media
from app.services import orders as svc
from app.services.ai import faq as faq_svc
from app.services.ai import matcher
from app.services.ai.i18n import TEXTS, norm_lang, t
from app.services.ai.mock_parser import detect_language
from app.services.ai.providers import (
    IMAGE_TYPES,
    MAX_IMAGE_BYTES,
    AIError,
    AIResult,
    get_provider,
    transcribe,
)
from app.services.ai.schema import Change, ExtractedLine, build_context
from app.services.pricing import PricingEngine

log = logging.getLogger("pinesphere.ai")

ACK = TEXTS["ack"]["en"]
QUESTION_RX = re.compile(r"\?|^\s*(what|when|where|which|who|how|why|do|does|can|is|are|will|enna|eppo|enga|kya|kab|"
                         r"kahan|kaise)\b", re.IGNORECASE)
ACK_REPEAT_MINUTES = 30
CATALOG_LIMIT = 500
CHANGE_MATCH_MIN = 0.7


def _fmt_qty(q) -> str:
    d = Decimal(str(q)).normalize()
    return f"{d:f}"


class Assistant:
    def __init__(self, db: Session, msg: Message):
        self.db = db
        self.msg = msg
        self.tenant = db.get(Tenant, msg.tenant_id)
        self.conv = db.get(Conversation, msg.conversation_id)
        self.customer = db.get(Customer, msg.customer_id)
        self.threshold = float(self.tenant.ai_match_threshold or Decimal("0.80"))
        self._products: list[Product] | None = None
        self._image: tuple[bytes, str] | None = None
        self.lang = detect_language(msg.text)

    # ---------- helpers ----------
    @property
    def products(self) -> list[Product]:
        if self._products is None:
            self._products = list(self.db.scalars(
                select(Product).where(Product.tenant_id == self.tenant.id, Product.is_active.is_(True))
                .order_by(Product.name).limit(CATALOG_LIMIT * 4)))
        return self._products

    def _open_quote(self) -> Order | None:
        return self.db.scalar(
            select(Order).where(Order.tenant_id == self.tenant.id, Order.customer_id == self.customer.id,
                                Order.status == OrderStatus.quoted)
            .order_by(Order.quoted_at.desc().nulls_last(), Order.created_at.desc()).limit(1).with_for_update())

    def _context(self, open_order: Order | None) -> str:
        catalog = [f"{p.sku} | {p.name} | {', '.join(p.aliases or [])} | {p.uom}" for p in self.products[:CATALOG_LIMIT]]
        quote = None
        if open_order is not None:
            quote = [f"{i}. {l.name} x {_fmt_qty(l.quantity)} {l.uom} (SKU {l.sku})"
                     for i, l in enumerate(open_order.lines, start=1)]
            quote.insert(0, f"Order {open_order.number}")
        return build_context(catalog, quote)

    def _log(self, kind: str, provider: str, model: str, *, intent: str | None = None, input_text: str | None = None,
             input_media: str | None = None, output: dict | None = None, input_tokens: int = 0,
             output_tokens: int = 0, cost: Decimal | None = None, latency_ms: int = 0,
             error: str | None = None) -> AiLog:
        row = AiLog(tenant_id=self.tenant.id, message_id=self.msg.id, kind=kind, provider=provider, model=model,
                    intent=intent, input_text=(input_text or "")[:4000] or None, input_media=input_media,
                    output=output, input_tokens=input_tokens, output_tokens=output_tokens, cost_usd=cost,
                    latency_ms=latency_ms, error=(error or None) and error[:2000])
        self.db.add(row)
        return row

    def reply(self, text: str) -> None:
        from app.services.whatsapp import outbound

        try:
            out = outbound.queue_text(self.db, self.customer, text, sender=SenderKind.bot)
            svc._pending(self.db).append(out.id)
        except outbound.SendBlocked as exc:
            log.info("Bot reply not sent: %s", exc.message)

    def acknowledge(self) -> None:
        recent = self.db.scalar(
            select(Message).where(Message.conversation_id == self.conv.id, Message.direction == Direction.outbound)
            .order_by(Message.created_at.desc()).limit(1))
        if (recent and recent.text in TEXTS["ack"].values()
                and recent.created_at > datetime.now(UTC) - timedelta(minutes=ACK_REPEAT_MINUTES)):
            return
        self.reply(t("ack", self.lang))

    def handover(self, reason: str, key: str = "handover") -> None:
        """FR-M10-05: stop the bot for this chat and flag it for staff (Inbox shows it under "Needs a person")."""
        self.conv.handover_at = datetime.now(UTC)
        self.conv.handover_reason = reason[:200]
        self.conv.bot_enabled = False
        self.reply(t(key, self.lang))

    def _mentions_other_customer(self) -> bool:
        """Buyer named another customer of this seller ("Kumar Stores outstanding?")."""
        text = (self.msg.text or "").strip().lower()
        if len(text) < 4:
            return False
        name = func.lower(Customer.business_name)
        return self.db.scalar(select(Customer.id).where(
            Customer.tenant_id == self.tenant.id, Customer.id != self.customer.id, func.length(Customer.business_name) >= 4,
            func.strpos(text, name) > 0).limit(1)) is not None

    # ---------- entry point ----------
    def run(self) -> None:
        msg = self.msg
        if not self.conv.bot_enabled or msg.direction != Direction.inbound:
            return
        if msg.msg_type not in (MessageType.text, MessageType.image, MessageType.audio):
            return
        if not self.tenant.ai_ordering_enabled:
            if msg.msg_type == MessageType.text and svc.is_status_query(msg.text):
                self.reply(svc.status_reply(self.db, self.customer))
            return

        try:
            provider = get_provider()
        except AIError as exc:
            self._log("extract", "unknown", "-", input_text=msg.text, error=str(exc))
            self.acknowledge()
            return

        text = msg.text
        image: tuple[bytes, str] | None = None
        media_kind = None
        try:
            if msg.msg_type == MessageType.audio:
                media_kind = msg.media_mime
                if not msg.media_path:
                    raise AIError("The voice note could not be downloaded")
                tr = transcribe(media.read(msg.media_path), msg.media_mime or "audio/ogg", msg.media_filename)
                self._log("transcribe", tr.provider, tr.model, input_media=msg.media_mime, output={"text": tr.text},
                          input_tokens=tr.input_tokens, output_tokens=tr.output_tokens, latency_ms=tr.latency_ms)
                msg.payload = {**(msg.payload or {}), "transcript": tr.text}
                text = tr.text
            elif msg.msg_type == MessageType.image:
                media_kind = msg.media_mime
                if not msg.media_path:
                    raise AIError("The photo could not be downloaded")
                if not provider.vision:
                    raise AIError("The development AI (mock) can't read photos. Set AI_PROVIDER to anthropic or openai.")
                mime = (msg.media_mime or "").split(";")[0]
                data = media.read(msg.media_path)
                if mime not in IMAGE_TYPES or len(data) > MAX_IMAGE_BYTES:
                    raise AIError("Photo format or size not supported (JPEG/PNG/WebP up to 5 MB)")
                image = (data, mime)
                self._image = image

            open_order = self._open_quote()
            result = provider.extract(text, self._context(open_order), image=image,
                                      has_open_quote=open_order is not None)
        except AIError as exc:
            self._log("transcribe" if msg.msg_type == MessageType.audio and "speech" in str(exc).lower() else "extract",
                      provider.name, provider.smart if image or media_kind else provider.fast,
                      input_text=text, input_media=media_kind, error=str(exc))
            self.acknowledge()
            return

        if result.extraction.language:
            self.lang = norm_lang(result.extraction.language) if self.lang == "en" else self.lang
        row = self._log("extract", result.provider, result.model, intent=result.extraction.intent, input_text=text,
                        input_media=media_kind, output=result.raw, input_tokens=result.input_tokens,
                        output_tokens=result.output_tokens, cost=result.cost_usd, latency_ms=result.latency_ms)
        self.route(result, open_order, row)

    # ---------- routing ----------
    def route(self, result: AIResult, open_order: Order | None, row: AiLog) -> None:
        ex = result.extraction
        about_others = ex.intent in ("order_status", "outstanding", "ledger", "invoice_copy") and (
            ex.other_party or self._mentions_other_customer())
        if about_others:  # buyers only ever see their own account (M10 safety rule)
            self.reply(t("not_yours", self.lang))
        elif ex.intent == "order_change" and open_order is not None and ex.changes:
            self.apply_changes(open_order, ex.changes, row)
        elif ex.intent in ("order", "order_change") and ex.lines:
            self.new_order(ex.lines, row)
        elif ex.intent == "order_status":
            self.reply(svc.status_reply(self.db, self.customer))
        elif ex.intent == "price_enquiry" and ex.lines:
            self.prices(ex.lines)
        elif ex.intent == "outstanding":
            self.outstanding()
        elif ex.intent == "payment":
            self.payment_proof()
        elif ex.intent == "ledger":
            self.send_statement()
        elif ex.intent == "invoice_copy":
            self.send_invoice_copy(ex.reference)
        elif ex.intent == "greeting":
            name = (self.customer.contact_person or "").split()
            self.reply(t("greeting", self.lang, name=f" {name[0]}" if name else ""))
        elif ex.intent == "human":
            self.handover("Buyer asked to talk to a person")
        elif ex.intent == "complaint":
            self.handover("Complaint", "complaint")
        elif self.msg.msg_type in (MessageType.image, MessageType.audio):
            self.acknowledge()  # media we couldn't use: staff will look
        else:
            self.general_question(row)

    def general_question(self, row: AiLog) -> None:
        """FR-M10-03 / 05: answer from the seller's FAQ, else hand over if it was a real question."""
        text = self.msg.payload.get("transcript") if self.msg.payload and self.msg.payload.get("transcript") \
            else self.msg.text
        entry, res = faq_svc.find(self.db, self.tenant.id, text)
        if res is not None:
            self._log("faq", res.provider, res.model, input_text=text, output=res.data, input_tokens=res.input_tokens,
                      output_tokens=res.output_tokens, cost=res.cost_usd, latency_ms=res.latency_ms)
        if entry is not None:
            entry.times_used = (entry.times_used or 0) + 1
            row.output = {**(row.output or {}), "faq_id": str(entry.id)}
            self.reply(faq_svc.answer_in(entry, self.lang))
        elif text and (QUESTION_RX.search(text) or len(text.split()) >= 5):
            self.handover("Question the bot couldn't answer")

    def outstanding(self) -> None:
        """FR-M10-02: this buyer's balance and unpaid invoices, straight from the ledger."""
        from app.services import invoices as inv_svc
        from app.services import payments as pay_svc

        bal = inv_svc.balance(self.db, self.customer)
        unpaid = pay_svc.unpaid_invoices(self.db, self.customer)
        if bal < 0 and not unpaid:
            self.reply(t("advance", self.lang, total=f"{-bal:,.2f}"))
            return
        if bal <= 0 and not unpaid:
            self.reply(t("outstanding_none", self.lang))
            return
        today = inv_svc.today()
        rows = []
        for inv in unpaid[:8]:
            due = inv.due_date or inv.invoice_date
            flag = t("overdue", self.lang) if due < today else t("due_on", self.lang)
            rows.append(f"• {inv.number}: ₹{pay_svc.balance_due(inv):,.2f} ({flag} {due:%d %b})")
        more = f"\n…+{len(unpaid) - 8}" if len(unpaid) > 8 else ""
        parts = [t("outstanding_head", self.lang, total=f"{max(bal, Decimal(0)):,.2f}")]
        if rows:
            parts.append(t("due_list", self.lang) + "\n" + "\n".join(rows) + more)
        if bal > 0:
            try:
                from app.models import LinkStatus, PaymentLink

                link = self.db.scalar(select(PaymentLink).where(
                    PaymentLink.customer_id == self.customer.id, PaymentLink.status == LinkStatus.created,
                    PaymentLink.amount == bal, PaymentLink.expires_at > datetime.now(UTC)).limit(1)) \
                    or pay_svc.create_link(self.db, None, self.customer, amount=bal, purpose="reminder")
                parts.append(t("pay_link", self.lang, url=link.short_url))
            except pay_svc.PaymentError as exc:  # no gateway set up: answer without a link
                log.info("No payment link in outstanding reply: %s", exc)
        parts.append(t("statement_hint", self.lang))
        self.reply("\n\n".join(parts))

    def _match(self, line: ExtractedLine):
        return matcher.best_match(line.text, self.products, line.sku, line.confidence)

    def new_order(self, lines: list[ExtractedLine], row: AiLog) -> None:
        items: dict = {}
        unclear: list[dict] = []
        for line in lines:
            product, conf, cands = self._match(line)
            ok = product is not None and conf >= self.threshold
            if ok and line.quantity and line.quantity > 0:
                qty = Decimal(str(line.quantity))
                if product.id in items:
                    items[product.id]["quantity"] += qty
                else:
                    items[product.id] = {"product": product, "quantity": qty, "raw_text": line.text[:300],
                                         "match_confidence": Decimal(str(conf)).quantize(Decimal("0.001"))}
                continue
            unclear.append({
                "text": line.text[:200], "quantity": line.quantity, "unit": line.unit,
                "reason": "quantity missing" if ok else "not sure which product",
                "candidates": [{"product_id": str(m.product.id), "sku": m.product.sku, "name": m.product.name,
                                "score": m.score} for m in cands],
            })
        if not items:
            self.reply(self._clarify_text(unclear))
            return
        try:
            order = svc.create_order(self.db, None, self.customer, list(items.values()), source=OrderSource.whatsapp,
                                     source_message_id=self.msg.id)
        except svc.OrderError as exc:
            row.error = f"Order not created: {exc.message}"
            self.acknowledge()
            return
        order.ai_unclear = unclear or None
        row.order_id = order.id
        svc.send_quote(self.db, None, order)

    def _clarify_text(self, unclear: list[dict]) -> str:
        rows = []
        for u in unclear[:8]:
            q = f" ({_fmt_qty(u['quantity'])})" if u.get("quantity") else ""
            names = [c["name"] for c in u["candidates"][:2]]
            if u["reason"] == "quantity missing" and names:
                rows.append(f"• {names[0]}: how many?")
            elif names:
                rows.append(f"• {u['text']}{q}: did you mean {' or '.join(names)}?")
            else:
                rows.append(f"• {u['text']}{q}: not found in our list")
        return ("Sorry, we couldn't place this order yet:\n" + "\n".join(rows)
                + "\n\nPlease reply with the exact item name and quantity, or our team will help you shortly.")

    def apply_changes(self, order: Order, changes: list[Change], row: AiLog) -> None:
        """FR-M5-07: "make salt 30", "remove item 2", "add 5 soap" -> revised quote."""
        current = [{"product": self.db.get(Product, l.product_id), "quantity": Decimal(l.quantity),
                    "raw_text": l.raw_text, "match_confidence": l.match_confidence, "remove": False}
                   for l in order.lines]
        applied, problems = 0, []

        def find(ch: Change) -> int | None:
            if ch.item_number and 1 <= ch.item_number <= len(current):
                return ch.item_number - 1
            if ch.sku:
                for i, c in enumerate(current):
                    if c["product"].sku.lower() == ch.sku.lower():
                        return i
            if ch.text:
                scored = [(matcher.score(ch.text, c["product"]), i) for i, c in enumerate(current)]
                scored.sort(reverse=True)
                if scored and scored[0][0] >= CHANGE_MATCH_MIN:
                    return scored[0][1]
            return None

        for ch in changes:
            if ch.action == "add":
                line = ExtractedLine(text=ch.text or "", quantity=ch.quantity or 1, sku=ch.sku,
                                     confidence=0.9 if ch.sku else 0)
                product, conf, _ = self._match(line)
                if product is None or conf < self.threshold:
                    problems.append(ch.text or "?")
                    continue
                existing = next((c for c in current if c["product"].id == product.id and not c["remove"]), None)
                if existing:
                    existing["quantity"] += Decimal(str(line.quantity))
                else:
                    current.append({"product": product, "quantity": Decimal(str(line.quantity)),
                                    "raw_text": line.text[:300], "match_confidence": Decimal(str(conf)).quantize(Decimal("0.001")),
                                    "remove": False})
                applied += 1
                continue
            idx = find(ch)
            if idx is None:
                problems.append(ch.text or (f"item {ch.item_number}" if ch.item_number else "?"))
                continue
            if ch.action == "remove" or (ch.action == "set_quantity" and (ch.quantity or 0) <= 0):
                current[idx]["remove"] = True
            elif ch.action == "set_quantity":
                current[idx]["quantity"] = Decimal(str(ch.quantity))
            applied += 1

        if not applied:
            self.reply("Sorry, we couldn't work out that change. Our team will update your quote shortly.")
            return
        remaining = [{k: v for k, v in c.items() if k != "remove"} for c in current if not c["remove"]]
        row.order_id = order.id
        if not remaining:
            svc.cancel(self.db, order, "Buyer removed all items on WhatsApp", by_buyer=True)
            return
        svc.edit_lines(self.db, None, order, remaining, actor="buyer")
        if problems:
            self.reply("Note: we couldn't find " + ", ".join(problems[:5]) + ". Our team will check.")

    def prices(self, lines: list[ExtractedLine]) -> None:
        engine = PricingEngine(self.db, self.customer)
        rows, first = [], None
        for line in lines[:10]:
            product, conf, cands = self._match(line)
            if product is None or conf < self.threshold:
                names = [c.product.name for c in cands[:2]]
                rows.append(f"• {line.text}: " + (f"did you mean {' or '.join(names)}?" if names else "not found"))
                continue
            first = first or product
            p = engine.price_line(product, Decimal(str(line.quantity or 1)))
            row = f"• {product.name}: ₹{p['unit_price']:,.2f} per {product.uom} + {Decimal(p['gst_rate']).normalize():f}% GST"
            if p["scheme_name"]:
                row += f" (offer: {p['scheme_name']})"
            if product.stock_status.value == "out_of_stock":
                row += " (currently out of stock)"
            rows.append(row)
        tail = f"\n\nTo order, reply with quantities, e.g. \"10 {first.name}\"." if first else ""
        self.reply("Your rates:\n" + "\n".join(rows) + tail)


    def payment_proof(self) -> None:
        """FR-M8-07: buyer says they paid / sends a screenshot -> proposal for Accounts to confirm."""
        from app.services import payments as pay_svc
        from app.services.ai.payment_reader import read_payment

        try:
            fields, res = read_payment(self.msg.text, self._image)
        except AIError as exc:
            self._log("payment_read", "mock", "-", input_text=self.msg.text, error=str(exc))
            self.acknowledge()
            return
        safe = {k: (str(v) if v is not None else None) for k, v in fields.items()}
        if res is not None:
            self._log("payment_read", res.provider, res.model, intent="payment", input_text=self.msg.text,
                      input_media=self._image[1] if self._image else None, output=safe,
                      input_tokens=res.input_tokens, output_tokens=res.output_tokens, cost=res.cost_usd,
                      latency_ms=res.latency_ms)
        if not fields.get("amount") and not fields.get("reference"):
            return  # nothing usable: staff handle it from the inbox
        _, reply = pay_svc.propose_from_buyer(self.db, self.customer, self.msg.id, {**fields, "raw": safe})
        self.reply(reply)

    def send_statement(self) -> None:
        """FR-M7-09: ledger PDF for the month asked for, else this financial year to date."""
        from app.services import invoices as inv_svc

        start, end, label = inv_svc.period_from_text(self.msg.text, self.tenant)
        msg, note = inv_svc.send_statement(self.db, self.customer, start, end, label)
        if msg is None:
            log.info("Statement not sent: %s", note)

    def send_invoice_copy(self, reference: str | None) -> None:
        """FR-M7-11: copy of an invoice by number (or the latest one)."""
        from app.services import invoices as inv_svc

        inv = inv_svc.find_customer_invoice(self.db, self.customer, reference)
        if inv is None:
            self.reply(f"We couldn't find invoice {reference} for your account. Please check the number, "
                       "or our team will help you shortly." if reference else
                       "We couldn't find any invoices for your account yet.")
            return
        if not inv.pdf_path:
            self.reply(f"Invoice {inv.number} dated {inv.invoice_date:%d %b %Y} for ₹{inv.total:,.2f}. "
                       "Our team will share the PDF shortly.")
            return
        inv_svc.send_invoice(self.db, inv, f"Copy of invoice {inv.number} dated {inv.invoice_date:%d %b %Y} "
                                           f"for ₹{inv.total:,.2f}.")


def process_message(db: Session, msg: Message) -> None:
    Assistant(db, msg).run()
