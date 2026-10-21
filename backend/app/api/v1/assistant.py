"""M10: business assistant, owner rules, daily summary, dashboard and the buyer-bot FAQ."""

import uuid
from datetime import date, datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import ALL_STAFF, MANAGERS, require_roles
from app.models import AiLog, AssistantMessage, AutomationRule, DailySummary, FaqEntry, Role, Tenant, User
from app.services import analytics as an
from app.services import audit, automation, excel
from app.services.ai import business
from app.services.ai import faq as faq_svc

router = APIRouter(tags=["Assistant"])
staff = require_roles(*ALL_STAFF)
managers = require_roles(*MANAGERS)
owner_only = require_roles(Role.owner)


# ---------------------------------------------------------------- schemas
class AskIn(BaseModel):
    question: str = Field(min_length=2, max_length=1000)


class MessageOut(BaseModel):
    id: uuid.UUID
    created_at: datetime
    question: str
    kind: str
    tool: str | None
    params: dict | None
    answer: str
    columns: list | None
    rows: list | None
    total_rows: int
    rule_id: uuid.UUID | None
    provider: str | None


class RuleOut(BaseModel):
    id: uuid.UUID
    kind: str
    description: str
    params: dict
    weekdays: list[int]
    hour: int
    is_active: bool
    last_run_on: date | None
    last_run_summary: str | None
    created_at: datetime


class RulePatch(BaseModel):
    is_active: bool


class FaqIn(BaseModel):
    question: str = Field(min_length=3, max_length=300)
    answer: str = Field(min_length=1, max_length=2000)
    answer_ta: str | None = Field(default=None, max_length=2000)
    answer_hi: str | None = Field(default=None, max_length=2000)
    keywords: list[str] = Field(default_factory=list, max_length=20)
    is_active: bool = True

    @field_validator("keywords")
    @classmethod
    def _kw(cls, v: list[str]) -> list[str]:
        return sorted({k.strip().lower()[:50] for k in v if k and k.strip()})

    @field_validator("answer_ta", "answer_hi")
    @classmethod
    def _blank(cls, v: str | None) -> str | None:
        return v.strip() if v and v.strip() else None


class FaqOut(FaqIn):
    id: uuid.UUID
    times_used: int
    created_at: datetime


class FaqTestIn(BaseModel):
    text: str = Field(min_length=1, max_length=500)


class SummaryOut(BaseModel):
    id: uuid.UUID
    summary_date: date
    data: dict
    text: str
    sent_to: str | None
    sent_at: datetime | None
    send_error: str | None
    created_at: datetime


def _jsonable(v):
    if isinstance(v, Decimal):
        return str(v)
    if isinstance(v, datetime):
        return v.isoformat(timespec="minutes")
    if isinstance(v, date):
        return v.isoformat()
    return v


def _msg_out(m: AssistantMessage) -> MessageOut:
    return MessageOut(id=m.id, created_at=m.created_at, question=m.question, kind=m.kind, tool=m.tool, params=m.params,
                      answer=m.answer, columns=m.columns, rows=m.rows, total_rows=m.total_rows, rule_id=m.rule_id,
                      provider=m.provider)


# ---------------------------------------------------------------- business assistant (FR-M10-06, 07, 09)
@router.post("/assistant/ask", response_model=MessageOut)
def ask(body: AskIn, db: Session = Depends(get_db), user: User = Depends(staff)):
    started = datetime.now()
    plan = business.plan_for(body.question)
    try:
        out = business.run(db, user, plan)
    except Exception as exc:  # never show a guess
        db.rollback()
        out = {"kind": "error", "text": "Sorry, something went wrong while looking that up. Please try again.",
               "columns": [], "rows": [], "total_rows": 0}
        plan.params = {**plan.params, "_error": str(exc)[:300]}
    params = {k: v for k, v in plan.params.items() if k != "period" or not isinstance(v, str) or len(v) < 40}
    if out.get("rule"):
        params = {**params, "rule": out["rule"]}
    m = AssistantMessage(
        tenant_id=user.tenant_id, user_id=user.id, question=body.question.strip(), kind=out["kind"], tool=plan.tool,
        params={k: _jsonable(v) for k, v in params.items()}, answer=out["text"], columns=out["columns"] or None,
        rows=[{k: _jsonable(v) for k, v in r.items()} for r in out["rows"]] or None, total_rows=out["total_rows"],
        provider=plan.provider)
    db.add(m)
    res = plan.raw
    db.add(AiLog(tenant_id=user.tenant_id, kind="assistant", provider=res.provider if res else "rules",
                 model=res.model if res else "-", intent=plan.tool, input_text=body.question[:4000],
                 output={"tool": plan.tool, "params": m.params}, input_tokens=res.input_tokens if res else 0,
                 output_tokens=res.output_tokens if res else 0, cost_usd=res.cost_usd if res else Decimal(0),
                 latency_ms=int((datetime.now() - started).total_seconds() * 1000)))
    db.commit()
    return _msg_out(m)


@router.get("/assistant/messages", response_model=list[MessageOut])
def history(limit: int = Query(default=30, le=100), db: Session = Depends(get_db), user: User = Depends(staff)):
    rows = db.scalars(select(AssistantMessage).where(AssistantMessage.user_id == user.id)
                      .order_by(AssistantMessage.created_at.desc()).limit(limit)).all()
    return [_msg_out(m) for m in reversed(rows)]


def _own_message(db: Session, user: User, message_id: uuid.UUID) -> AssistantMessage:
    m = db.scalar(select(AssistantMessage).where(AssistantMessage.id == message_id,
                                                 AssistantMessage.user_id == user.id))
    if m is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Answer not found")
    return m


@router.get("/assistant/messages/{message_id}/export")
def export(message_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(staff)):
    m = _own_message(db, user, message_id)
    if not m.columns:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "This answer has no table")

    def cell(v, typ):
        if v is None:
            return None
        if typ in ("money", "number"):
            return float(v)
        if typ == "date":
            return date.fromisoformat(v)
        if typ == "datetime":
            return datetime.fromisoformat(v)
        return v

    data = [[cell(r.get(c["key"]), c["type"]) for c in m.columns] for r in (m.rows or [])]
    name = (m.tool or "answer").replace("_", "-")
    return Response(excel.export_rows([c["label"] for c in m.columns], data, "Answer"), media_type=excel.XLSX_MIME,
                    headers={"Content-Disposition": f'attachment; filename="{name}.xlsx"'})


@router.post("/assistant/messages/{message_id}/confirm-rule", response_model=RuleOut)
def confirm_rule(message_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(owner_only)):
    """FR-M10-09: the rule exists only after the owner confirms the proposal."""
    m = _own_message(db, user, message_id)
    if m.kind != "rule_proposal" or not (m.params or {}).get("rule"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "This answer is not a rule proposal")
    if m.rule_id:
        raise HTTPException(status.HTTP_409_CONFLICT, "This rule was already created")
    r = m.params["rule"]
    rule = AutomationRule(tenant_id=user.tenant_id, kind=r["kind"], description=r["description"], params=r["params"],
                          weekdays=r["weekdays"], hour=r["hour"], is_active=True, created_by_user_id=user.id)
    db.add(rule)
    db.flush()
    m.rule_id = rule.id
    audit.record(db, user, "automation_rule", rule.id, "create", new=audit.snapshot(rule))
    db.commit()
    return RuleOut.model_validate(rule, from_attributes=True)


@router.get("/assistant/rules", response_model=list[RuleOut])
def list_rules(db: Session = Depends(get_db), user: User = Depends(managers)):
    rows = db.scalars(select(AutomationRule).where(AutomationRule.tenant_id == user.tenant_id)
                      .order_by(AutomationRule.created_at.desc())).all()
    return [RuleOut.model_validate(r, from_attributes=True) for r in rows]


def _rule(db: Session, user: User, rule_id: uuid.UUID) -> AutomationRule:
    r = db.scalar(select(AutomationRule).where(AutomationRule.id == rule_id, AutomationRule.tenant_id == user.tenant_id))
    if r is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Rule not found")
    return r


@router.patch("/assistant/rules/{rule_id}", response_model=RuleOut)
def update_rule(rule_id: uuid.UUID, body: RulePatch, db: Session = Depends(get_db), user: User = Depends(owner_only)):
    r = _rule(db, user, rule_id)
    r.is_active = body.is_active
    audit.record(db, user, "automation_rule", r.id, "update", new={"is_active": body.is_active})
    db.commit()
    return RuleOut.model_validate(r, from_attributes=True)


@router.delete("/assistant/rules/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_rule(rule_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(owner_only)):
    r = _rule(db, user, rule_id)
    audit.record(db, user, "automation_rule", r.id, "delete", audit.snapshot(r))
    db.delete(r)
    db.commit()


@router.post("/assistant/rules/{rule_id}/run", response_model=RuleOut)
def run_rule_now(rule_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(owner_only)):
    from app.services import orders as order_svc

    r = _rule(db, user, rule_id)
    automation.run_rule(db, r, an.today())
    order_svc.commit_and_send(db)
    return RuleOut.model_validate(r, from_attributes=True)


# ---------------------------------------------------------------- daily summary (FR-M10-08)
@router.get("/assistant/summaries", response_model=list[SummaryOut])
def summaries(limit: int = Query(default=7, le=60), db: Session = Depends(get_db), user: User = Depends(managers)):
    rows = db.scalars(select(DailySummary).where(DailySummary.tenant_id == user.tenant_id)
                      .order_by(DailySummary.summary_date.desc()).limit(limit)).all()
    return [SummaryOut.model_validate(s, from_attributes=True) for s in rows]


@router.post("/assistant/summaries/send-now", response_model=SummaryOut)
def send_summary_now(db: Session = Depends(get_db), user: User = Depends(managers)):
    tenant = db.get(Tenant, user.tenant_id)
    s = automation.make_summary(db, tenant, an.today())
    db.commit()
    return SummaryOut.model_validate(s, from_attributes=True)


# ---------------------------------------------------------------- dashboard (FR-M10-10)
@router.get("/dashboard")
def dashboard(period: str = Query(default="this_month"), start: date | None = None, end: date | None = None,
              db: Session = Depends(get_db), user: User = Depends(staff)):
    if start and end and (end < start or (end - start).days > 366):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Pick a range of up to one year")
    p = an.period(period, start=start, end=end)
    return {**an.dashboard(db, user, p), "scope": "own customers" if user.role == Role.sales else "company"}


# ---------------------------------------------------------------- buyer-bot FAQ (FR-M10-03)
@router.get("/faqs", response_model=list[FaqOut])
def list_faqs(db: Session = Depends(get_db), user: User = Depends(staff)):
    rows = db.scalars(select(FaqEntry).where(FaqEntry.tenant_id == user.tenant_id).order_by(FaqEntry.created_at)).all()
    return [FaqOut.model_validate(f, from_attributes=True) for f in rows]


@router.post("/faqs", response_model=FaqOut, status_code=status.HTTP_201_CREATED)
def create_faq(body: FaqIn, db: Session = Depends(get_db), user: User = Depends(managers)):
    f = FaqEntry(tenant_id=user.tenant_id, **body.model_dump())
    db.add(f)
    db.flush()
    audit.record(db, user, "faq", f.id, "create", new=audit.snapshot(f))
    db.commit()
    return FaqOut.model_validate(f, from_attributes=True)


def _faq(db: Session, user: User, faq_id: uuid.UUID) -> FaqEntry:
    f = db.scalar(select(FaqEntry).where(FaqEntry.id == faq_id, FaqEntry.tenant_id == user.tenant_id))
    if f is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "FAQ not found")
    return f


@router.put("/faqs/{faq_id}", response_model=FaqOut)
def update_faq(faq_id: uuid.UUID, body: FaqIn, db: Session = Depends(get_db), user: User = Depends(managers)):
    f = _faq(db, user, faq_id)
    old = audit.snapshot(f)
    for k, v in body.model_dump().items():
        setattr(f, k, v)
    audit.record(db, user, "faq", f.id, "update", old, audit.snapshot(f))
    db.commit()
    return FaqOut.model_validate(f, from_attributes=True)


@router.delete("/faqs/{faq_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_faq(faq_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(managers)):
    f = _faq(db, user, faq_id)
    audit.record(db, user, "faq", f.id, "delete", audit.snapshot(f))
    db.delete(f)
    db.commit()


@router.post("/faqs/test")
def test_faq(body: FaqTestIn, db: Session = Depends(get_db), user: User = Depends(staff)):
    """What the bot would answer to this message."""
    from app.services.ai.mock_parser import detect_language

    f, _ = faq_svc.find(db, user.tenant_id, body.text)
    lang = detect_language(body.text)
    return {"faq_id": str(f.id) if f else None, "question": f.question if f else None,
            "answer": faq_svc.answer_in(f, lang) if f else None, "language": lang}
