"""Discovery questions and decisions: who answers what, the answers, and the decisions they make.

A question comes from an extraction round (the Business Analyst's gaps) and belongs to the role that
discovery.question_owners gives its kind. Answering it records an accepted decision. Minutes of a meeting give
proposed decisions, which count only after the role that owns them accepts them.
"""

from collections import defaultdict
from datetime import datetime, timezone
import re
import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from controller.app.discovery.inventory import Gap
from controller.app.events import record_event
from controller.app.models import Clarification, Decision, Run
from controller.app.projects.config import Discovery
from controller.app.states import RunKind, RunStatus

OPEN, ANSWERED, DEFERRED, WITHDRAWN = "open", "answered", "deferred", "withdrawn"


def _norm(text: str) -> str:
    return re.sub(r"\W+", " ", text.lower()).strip()


# A suggestion is an answer the role can accept. One that tells the role what to do ("Define the steps", "Cung cấp
# ngưỡng"), or repeats the question, says nothing: 23 of 25 suggestions of the attendance discovery were like that.
DIRECTIVE = re.compile(r"^(xác định|xác nhận|cung cấp|mô tả|nêu|làm rõ|liệt kê|quy định|lập|chọn|ánh xạ|gắn|giữ là|"
                       r"define|provide|describe|specify|clarify|list|confirm|decide|choose|identify|determine|state)\b",
                       re.IGNORECASE)


def useful_suggestion(suggestion: str | None, question: str) -> str | None:
    text = " ".join((suggestion or "").split())
    if not text or DIRECTIVE.match(text):
        return None
    words, asked = set(_norm(text).split()), set(_norm(question).split())
    if words and len(words & asked) / len(words) > 0.8:  # mostly the question's own words
        return None
    return text


def owner_for(kind: str, config: Discovery) -> str:
    return config.question_owners.get(kind) or config.approvers.get("baseline") or config.roles[0].id


def role_name(role: str, config: Discovery) -> str:
    return next((r.name for r in config.roles if r.id == role), role)


def questions(session: Session, run_id: uuid.UUID) -> list[Clarification]:
    return list(session.scalars(select(Clarification).where(Clarification.run_id == run_id,
                                                           Clarification.status.is_not(None))
                                .order_by(Clarification.position)))


def number(run_questions: list[Clarification], question: Clarification) -> str:
    return f"Q-{run_questions.index(question) + 1:03d}"


def decisions(session: Session, run_id: uuid.UUID) -> list[Decision]:
    return list(session.scalars(select(Decision).where(Decision.run_id == run_id).order_by(Decision.number)))


LATER_REASON = "for the SRS, SDS or a story's spec to settle (the Business Analyst marked it later)"


def sync_questions(session: Session, run: Run, gaps: list[Gap], round_: int, config: Discovery,
                   ids_by_name: dict[str, str] | None = None) -> tuple[int, int, int]:
    """Add the round's questions that are not asked yet: baseline questions first (blocking first), up to
    max_questions_per_round; questions the Business Analyst marked later are deferred at once, so the Product Owner
    answers only what the baseline needs and the SRS run gets the rest. Item names a part's gap gives become REQ ids.
    Returns (added, left out by the limit, deferred as later)."""
    ids_by_name = ids_by_name or {}
    existing = questions(session, run.id)
    asked = {_norm(q.question) for q in existing}
    fresh, seen = [], set()
    for gap in sorted(gaps, key=lambda g: (g.stage != "baseline", not g.blocking)):
        key = _norm(gap.question)
        if key and key not in asked and key not in seen:
            seen.add(key)
            fresh.append(gap)
    limit = config.limits.max_questions_per_round
    kept = fresh[:limit]
    for position, gap in enumerate(kept, start=len(existing) + 1):
        later = gap.stage == "later" and not gap.blocking
        session.add(Clarification(run_id=run.id, position=position, question=gap.question, blocking=gap.blocking,
                                  kind=gap.kind, owner_role=owner_for(gap.kind, config),
                                  status=DEFERRED if later else OPEN, status_reason=LATER_REASON if later else None,
                                  why=gap.why or None, suggestion=useful_suggestion(gap.suggestion, gap.question),
                                  items=[ids_by_name.get(_norm(i), i) for i in gap.items],
                                  citations=[c.model_dump() for c in gap.sources], round=round_))
    session.flush()
    deferred = sum(1 for g in kept if g.stage == "later" and not g.blocking)
    return len(kept) - deferred, max(0, len(fresh) - limit), deferred


def _require(run: Run) -> None:
    if run.kind != RunKind.DISCOVERY:
        raise ValueError("questions and decisions belong to a discovery run")
    if run.status != RunStatus.CLARIFYING:
        raise ValueError(f"questions can be answered while the discovery is CLARIFYING; it is {run.status}")


def _question(session: Session, run: Run, question_id: uuid.UUID) -> Clarification:
    question = session.get(Clarification, question_id)
    if question is None or question.run_id != run.id or question.status is None:
        raise LookupError("question not found")
    return question


def _next_number(session: Session, run_id: uuid.UUID) -> int:
    return (session.scalar(select(func.max(Decision.number)).where(Decision.run_id == run_id)) or 0) + 1


def decision_id(decision: Decision) -> str:
    return f"D-{decision.number:03d}"


def answer(session: Session, run: Run, question_id: uuid.UUID, text: str, *, actor: str) -> Decision:
    """The owner role answers; the answer is an accepted decision about the question's items."""
    _require(run)
    question = _question(session, run, question_id)
    if question.status != OPEN:
        raise ValueError(f"the question is {question.status}; only open questions are answered")
    if not text.strip():
        raise ValueError("write the answer first")
    now = datetime.now(timezone.utc)
    question.answer, question.answered_at, question.answered_by, question.status = text.strip(), now, actor, ANSWERED
    decision = Decision(run_id=run.id, number=_next_number(session, run.id), text=text.strip(), basis="answer",
                        question_id=question.id, items=list(question.items or []), role=question.owner_role,
                        status="accepted", proposed_by=actor, decided_by=actor, decided_at=now)
    session.add(decision)
    session.flush()
    record_event(session, "question.answered", actor=actor, acting_role=question.owner_role, run_id=run.id,
                 payload={"question": str(question.id), "decision": decision_id(decision)})
    return decision


def set_status(session: Session, run: Run, question_id: uuid.UUID, status: str, *, actor: str, reason: str) -> None:
    """Defer a question to a later phase, withdraw one that does not apply, or open it again."""
    _require(run)
    if status not in (OPEN, DEFERRED, WITHDRAWN):
        raise ValueError("a question can be deferred, withdrawn or opened again")
    if not reason.strip():
        raise ValueError(f"say why the question is {status}")
    question = _question(session, run, question_id)
    if question.status == ANSWERED:
        raise ValueError("the question is answered already")
    question.status, question.status_reason = status, reason.strip()
    record_event(session, f"question.{status}", actor=actor, acting_role=question.owner_role, run_id=run.id,
                 payload={"question": str(question.id), "reason": reason})


def reassign(session: Session, run: Run, question_id: uuid.UUID, role: str, config: Discovery, *, actor: str,
             reason: str) -> None:
    _require(run)
    if role not in {r.id for r in config.roles}:
        raise ValueError(f"{role} is not a role of discovery.roles")
    question = _question(session, run, question_id)
    if question.status != OPEN:
        raise ValueError("only open questions move to another role")
    record_event(session, "question.reassigned", actor=actor, acting_role=question.owner_role, run_id=run.id,
                 payload={"question": str(question.id), "from": question.owner_role, "to": role, "reason": reason})
    question.owner_role, question.status_reason = role, reason.strip() or None


EFFECTS = ("confirmed", "out_of_scope", "changed")


def record(session: Session, run: Run, text: str, items: list[str], role: str, config: Discovery, *,
           actor: str, effect: str = "", new_text: str = "") -> Decision:
    """A decision a role takes directly, such as putting an item out of scope; it counts at once. With an effect,
    the controller applies it to the items at the next extraction exactly as stated."""
    _require(run)
    if not text.strip():
        raise ValueError("write the decision first")
    if effect and effect not in EFFECTS:
        raise ValueError(f"effect must be one of {', '.join(EFFECTS)}")
    if effect and not [i for i in items if i.strip()]:
        raise ValueError("name the REQ ids the effect applies to")
    if effect == "changed" and not new_text.strip():
        raise ValueError("give the new requirement text of a changed item")
    if role not in {r.id for r in config.roles}:
        raise ValueError(f"{role} is not a role of discovery.roles")
    now = datetime.now(timezone.utc)
    decision = Decision(run_id=run.id, number=_next_number(session, run.id), text=text.strip(), basis="direct",
                        items=[i.strip() for i in items if i.strip()], role=role, status="accepted", proposed_by=actor,
                        decided_by=actor, decided_at=now, effect=effect or None, new_text=new_text.strip() or None)
    session.add(decision)
    session.flush()
    record_event(session, "decision.recorded", actor=actor, acting_role=role, run_id=run.id,
                 payload={"decision": decision_id(decision), "items": decision.items})
    return decision


def propose(session: Session, run: Run, text: str, *, question: Clarification | None, items: list[str],
            source_id: uuid.UUID, location: str, config: Discovery) -> Decision:
    """A decision the Business Analyst read in minutes; the question's owner (or the baseline approver) decides."""
    decision = Decision(run_id=run.id, number=_next_number(session, run.id), text=text, basis="minutes",
                        question_id=question.id if question else None, source_id=source_id, location=location,
                        items=items, role=question.owner_role if question else owner_for("scope", config),
                        status="proposed", proposed_by="business_analyst")
    session.add(decision)
    session.flush()
    return decision


def decide(session: Session, run: Run, number_: int, accept: bool, *, actor: str, reason: str) -> None:
    _require(run)
    decision = session.scalar(select(Decision).where(Decision.run_id == run.id, Decision.number == number_))
    if decision is None:
        raise LookupError("decision not found")
    if decision.status != "proposed":
        raise ValueError(f"{decision_id(decision)} is {decision.status} already")
    if not accept and not reason.strip():
        raise ValueError("say why the decision is rejected")
    now = datetime.now(timezone.utc)
    decision.status, decision.decided_by, decision.decided_at = ("accepted" if accept else "rejected"), actor, now
    decision.reason = reason.strip() or None
    if accept and decision.question_id:
        question = session.get(Clarification, decision.question_id)
        if question.status == OPEN:
            question.answer, question.answered_at, question.answered_by = decision.text, now, actor
            question.status = ANSWERED
    record_event(session, f"decision.{decision.status}", actor=actor, acting_role=decision.role, run_id=run.id,
                 payload={"decision": decision_id(decision), "reason": reason})


def apply_suggestions(session: Session, run: Run, suggested: list[dict]) -> int:
    """Drop suggestions of open questions that say nothing, then take the Business Analyst's new ones for open
    questions without one. Returns how many questions got a suggestion."""
    run_questions = questions(session, run.id)
    by_number = {number(run_questions, q): q for q in run_questions}
    for question in run_questions:
        if question.status == OPEN:
            question.suggestion = useful_suggestion(question.suggestion, question.question)
    given = 0
    for entry in suggested:
        question = by_number.get(entry.get("question", ""))
        text = useful_suggestion(entry.get("suggestion"), question.question) if question else None
        if question and question.status == OPEN and not question.suggestion and text:
            question.suggestion, given = text, given + 1
    return given


def open_count(session: Session, run_id: uuid.UUID) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for question in questions(session, run_id):
        if question.status == OPEN:
            counts[question.owner_role] += 1
    return dict(counts)


def render_questions(run_questions: list[Clarification], config: Discovery, title: str) -> str:
    cell = lambda s: " ".join(str(s or "").split()).replace("|", "\\|") or "—"
    lines = [f"# Questions: {title}", ""]
    by_role: dict[str, list[Clarification]] = defaultdict(list)
    for question in run_questions:
        by_role[question.owner_role].append(question)
    for role in [r.id for r in config.roles if r.id in by_role] + [r for r in by_role if r not in {x.id for x in config.roles}]:
        lines += [f"## {role_name(role, config)}", "", "| # | Kind | Question | About | Blocking | Status | Answer |",
                  "|---|---|---|---|---|---|---|"]
        for question in by_role[role]:
            status = question.status + (f" ({question.status_reason})" if question.status in (DEFERRED, WITHDRAWN) else "")
            lines.append(f"| {number(run_questions, question)} | {question.kind} | {cell(question.question)} | "
                         f"{cell(', '.join(question.items or []))} | {'yes' if question.blocking else 'no'} | {cell(status)} | "
                         f"{cell(question.answer)} |")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def workshop(run_questions: list[Clarification], config: Discovery, roles: list[str], title: str) -> str:
    """The open questions of the roles that attend a meeting, blocking first; no model is called."""
    lines = [f"# Workshop questions: {title}", ""]
    for role in [r.id for r in config.roles if not roles or r.id in roles]:
        open_ = sorted([q for q in run_questions if q.owner_role == role and q.status == OPEN], key=lambda q: not q.blocking)
        if not open_:
            continue
        lines += [f"## {role_name(role, config)}", ""]
        for question in open_:
            about = f" (about {', '.join(question.items)})" if question.items else ""
            lines.append(f"- [ ] **{number(run_questions, question)}**{' [blocking]' if question.blocking else ''} "
                         f"{question.question}{about}")
            if question.suggestion:
                lines.append(f"  - Suggestion: {question.suggestion}")
        lines.append("")
    if len(lines) == 2:
        lines.append("No open questions for these roles.")
    return "\n".join(lines).rstrip() + "\n"


def render_decisions(run_decisions: list[Decision], run_questions: list[Clarification], config: Discovery,
                     source_names: dict[str, str], title: str) -> str:
    cell = lambda s: " ".join(str(s or "").split()).replace("|", "\\|") or "—"
    by_id = {q.id: q for q in run_questions}
    lines = [f"# Decisions: {title}", "", "| # | Decision | About | Basis | Owner | Status | Decided by |",
             "|---|---|---|---|---|---|---|"]
    for decision in run_decisions:
        question = by_id.get(decision.question_id)
        basis = (f"answer to {number(run_questions, question)}" if decision.basis == "answer" and question
                 else "taken directly" if decision.basis == "direct"
                 else f"minutes {source_names.get(str(decision.source_id), '')} {decision.location or ''}".strip())
        if decision.basis == "minutes" and question:
            basis += f", answers {number(run_questions, question)}"
        lines.append(f"| {decision_id(decision)} | {cell(decision.text)} | {cell(', '.join(decision.items))} | {cell(basis)} | "
                     f"{cell(role_name(decision.role, config))} | {decision.status} | {cell(decision.decided_by)} |")
    if not run_decisions:
        lines.append("| — | No decisions yet | | | | | |")
    return "\n".join(lines) + "\n"


def as_dicts(session: Session, run_id: uuid.UUID) -> tuple[list[dict], list[dict]]:
    """Questions and decisions as the page and the baseline gate read them."""
    run_questions = questions(session, run_id)
    by_id = {x.id: x for x in run_questions}
    qs = [{"id": str(x.id), "number": number(run_questions, x), "kind": x.kind, "question": x.question, "why": x.why,
           "suggestion": x.suggestion, "items": x.items or [], "citations": x.citations or [], "blocking": x.blocking,
           "owner_role": x.owner_role, "status": x.status, "status_reason": x.status_reason, "answer": x.answer,
           "answered_by": x.answered_by, "round": x.round} for x in run_questions]
    ds = [{"number": d.number, "id": decision_id(d), "text": d.text, "basis": d.basis,
           "question": number(run_questions, by_id[d.question_id]) if d.question_id in by_id else None,
           "source": str(d.source_id) if d.source_id else None, "location": d.location, "items": d.items,
           "role": d.role, "status": d.status, "proposed_by": d.proposed_by, "decided_by": d.decided_by,
           "reason": d.reason, "effect": d.effect, "new_text": d.new_text} for d in decisions(session, run_id)]
    return qs, ds
