"""Decisions the operator makes in a human role. Each one is recorded with the acting role."""

from datetime import datetime, timezone
import json
import uuid

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from controller.app import coordinator
from controller.app.documents import add_document, add_plan, latest_document, latest_plan
from controller.app.events import record_event
from controller.app.models import Approval, Clarification, Project, PullRequest, Run, Task
from controller.app.schemas import Intent, PlanPayload, TestPlan, parse_criteria, render_intent
from controller.app.state_machine import transition_run, transition_task
from controller.app.config import get_settings
from controller.app.states import TASK_TRANSITIONS, TERMINAL, HumanRole, RunStatus, TaskStatus, TeamRole

PO, TL, CO = HumanRole.PRODUCT_OWNER.value, HumanRole.TECH_LEAD.value, HumanRole.CODE_OWNER.value

FAILURE_CATEGORIES = ("AMBIGUOUS_SPEC", "MISSING_CONTEXT", "BAD_GROUNDING", "WEAK_TESTS", "MODEL_LIMITATION",
                      "INFRASTRUCTURE", "OTHER")
FAILURE_STAGES = ("preflight", "specification", "planning", "test_design", "grounding", "test_writing", "coding",
                  "validation", "review", "pr", "ci")
FAILURE_ROLES = ("business_analyst", "tech_lead_ai", "qa_analyst", "qa_engineer", "developer", "validation_gate",
                 "code_reviewer", "release_engineer", "delivery_coordinator", "human")


class DecisionError(ValueError):
    """The decision does not fit the run's current state or artifact version."""


def _require(run: Run, *statuses: RunStatus) -> None:
    if run.status not in statuses:
        raise DecisionError(f"run is {run.status}; this decision needs {' or '.join(statuses)}")


def _approval(session: Session, run: Run, stage: str, decision: str, actor: str, role: str, *,
              reason: str | None = None, document_id=None, plan_id=None) -> None:
    session.add(Approval(run_id=run.id, stage=stage, decision=decision, actor=actor, acting_role=role,
                         reason=reason, document_id=document_id, plan_id=plan_id))
    record_event(session, f"{stage}.{decision}", actor=actor, acting_role=role, run_id=run.id,
                 payload={"reason": reason, "document": str(document_id) if document_id else None,
                          "plan": str(plan_id) if plan_id else None})


def _corrected(run: Run) -> None:
    run.human_corrected = True


def get_run(session: Session, run_id: uuid.UUID) -> Run:
    run = session.get(Run, run_id, with_for_update=True)
    if run is None:
        raise LookupError("run not found")
    return run


# ---------------------------------------------------------------- Product Owner

def create_run(session: Session, project: Project, intent: Intent, *, actor: str, idempotency_key: str,
               experiment: bool = False, base_commit: str | None = None, stage_overrides: dict | None = None,
               eval_run_id: uuid.UUID | None = None, eval_case: str | None = None) -> Run:
    existing = session.scalar(select(Run).where(Run.idempotency_key == idempotency_key))
    if existing:
        return existing
    if project.readiness == "not_ready":
        raise DecisionError(f"project {project.key} is not ready; open Projects and run the check")
    run = Run(project_id=project.id, title=intent.title, idempotency_key=idempotency_key,
              status=RunStatus.RECEIVED.value, experiment=experiment, base_commit=base_commit,
              stage_overrides=stage_overrides or {}, eval_run_id=eval_run_id, eval_case=eval_case)
    session.add(run)
    session.flush()
    add_document(session, run.id, "intent", render_intent(intent), author_kind="human", author=actor,
                 data=intent.model_dump())
    record_event(session, "intent.accepted", actor=actor, acting_role=PO, run_id=run.id, project_id=project.id)
    transition_run(session, run, RunStatus.PREFLIGHT, actor=actor, acting_role=PO)
    coordinator.queue(session, run, "preflight")
    return run


def answer_questions(session: Session, run: Run, answers: dict[str, str], *, actor: str) -> None:
    _require(run, RunStatus.NEEDS_CLARIFICATION)
    open_questions = list(session.scalars(select(Clarification).where(Clarification.run_id == run.id,
                                                                       Clarification.answer.is_(None))))
    for question in open_questions:
        text = answers.get(str(question.id), "").strip()
        if not text:
            raise DecisionError(f"answer every blocking question; missing: {question.question}")
        question.answer, question.answered_at = text, datetime.now(timezone.utc)
    record_event(session, "clarification.answered", actor=actor, acting_role=PO, run_id=run.id,
                 payload={"answers": len(open_questions)})
    transition_run(session, run, RunStatus.SPECIFYING, actor=actor, acting_role=PO)
    coordinator.queue(session, run, "specify")


def _current_spec(session: Session, run: Run, document_id: uuid.UUID, sha256: str):
    spec = latest_document(session, run.id, "spec")
    if spec is None or spec.id != document_id or spec.sha256 != sha256:
        raise DecisionError("this is not the latest spec version; reload and review it again")
    return spec


def approve_spec(session: Session, run: Run, document_id: uuid.UUID, sha256: str, *, actor: str) -> None:
    _require(run, RunStatus.WAITING_SPEC_APPROVAL)
    spec = _current_spec(session, run, document_id, sha256)
    _approval(session, run, "spec", "approve", actor, PO, document_id=spec.id)
    coordinator.accept_spec(session, run, spec, actor=actor, acting_role=PO)


def revise_spec(session: Session, run: Run, content: str, *, actor: str, reason: str | None) -> None:
    _require(run, RunStatus.WAITING_SPEC_APPROVAL)
    try:
        parse_criteria(content)
    except ValueError as exc:
        raise DecisionError(str(exc)) from exc
    document = add_document(session, run.id, "spec", content, author_kind="human", author=actor)
    _approval(session, run, "spec", "revise", actor, PO, reason=reason, document_id=document.id)
    _corrected(run)


def reject_spec(session: Session, run: Run, *, actor: str, reason: str, next_step: str) -> None:
    _require(run, RunStatus.WAITING_SPEC_APPROVAL)
    if not reason.strip():
        raise DecisionError("a rejection needs a reason")
    _approval(session, run, "spec", "reject", actor, PO, reason=reason)
    _corrected(run)
    if next_step == "cancel":
        transition_run(session, run, RunStatus.CANCELLED, actor=actor, acting_role=PO, reason=reason)
    else:
        transition_run(session, run, RunStatus.SPECIFYING, actor=actor, acting_role=PO, reason=reason)
        coordinator.queue(session, run, "specify")


# ---------------------------------------------------------------- Tech Lead

def _current_plan(session: Session, run: Run, plan_id: uuid.UUID, sha256: str):
    plan = latest_plan(session, run.id)
    if plan is None or plan.id != plan_id or plan.sha256 != sha256:
        raise DecisionError("this is not the latest plan version; reload and review it again")
    return plan


def approve_plan(session: Session, run: Run, plan_id: uuid.UUID, sha256: str, *, actor: str) -> None:
    _require(run, RunStatus.WAITING_PLAN_APPROVAL)
    plan = _current_plan(session, run, plan_id, sha256)
    _approval(session, run, "plan", "approve", actor, TL, plan_id=plan.id)
    plan.commit_sha = coordinator.commit_thread(session, run, "plan", coordinator.plan_markdown(run, plan),
                                                f"sdlc: plan v{plan.version} approved")
    for task in PlanPayload.model_validate(plan.payload).tasks:
        session.add(Task(run_id=run.id, plan_id=plan.id, external_task_id=task.id, payload=task.model_dump(),
                         status=TaskStatus.PENDING.value))
    session.flush()
    transition_run(session, run, RunStatus.READY, actor=actor, acting_role=TL)
    project = session.get(Project, run.project_id)
    if project.readiness != "ready" and not get_settings().coding_is_mocked:
        coordinator.to_needs_human(session, run, f"project {project.key} is {project.readiness}; coding needs ready")
        return
    coordinator.enter_stage(session, run, "coding", actor=actor, acting_role=TL)


def revise_plan(session: Session, run: Run, payload: dict, test_plan: dict, *, actor: str,
                reason: str | None) -> None:
    _require(run, RunStatus.WAITING_PLAN_APPROVAL)
    try:
        plan_model, tests_model = PlanPayload.model_validate(payload), TestPlan.model_validate(test_plan)
    except ValidationError as exc:
        error = exc.errors()[0]
        raise DecisionError(f"{'.'.join(str(p) for p in error['loc'])}: {error['msg']}") from exc
    plan = add_plan(session, run.id, plan_model.model_dump(), tests_model.model_dump(), author_kind="human",
                    author=actor)
    coordinator.ground(session, run, plan)
    _approval(session, run, "plan", "revise", actor, TL, reason=reason, plan_id=plan.id)
    _corrected(run)


def reject_plan(session: Session, run: Run, *, actor: str, reason: str, next_step: str) -> None:
    _require(run, RunStatus.WAITING_PLAN_APPROVAL)
    if not reason.strip():
        raise DecisionError("a rejection needs a reason")
    _approval(session, run, "plan", "reject", actor, TL, reason=reason)
    _corrected(run)
    if next_step == "cancel":
        transition_run(session, run, RunStatus.CANCELLED, actor=actor, acting_role=TL, reason=reason)
    else:
        coordinator_replan(session, run, actor=actor, reason=reason)


def coordinator_replan(session: Session, run: Run, *, actor: str, reason: str) -> None:
    transition_run(session, run, RunStatus.PLANNING, actor=actor, acting_role=TL, reason=reason)
    coordinator.queue(session, run, "plan")


# ---------------------------------------------------------------- any role

def start_stage(session: Session, run: Run, stage: str, *, actor: str, acting_role: str) -> None:
    if run.awaiting_start != stage:
        raise DecisionError(f"stage {stage} is not waiting to be started")
    run.awaiting_start = None
    record_event(session, "stage.started", actor=actor, acting_role=acting_role, run_id=run.id,
                 payload={"stage": stage})
    coordinator.schedule_stage(session, run, stage)


def cancel(session: Session, run: Run, *, actor: str, reason: str) -> None:
    if run.status in TERMINAL:
        raise DecisionError(f"run is already {run.status}")
    transition_run(session, run, RunStatus.CANCELLED, actor=actor, acting_role=TL, reason=reason or "cancelled")
    for task in session.scalars(select(Task).where(Task.run_id == run.id)):
        if TaskStatus.CANCELLED in TASK_TRANSITIONS[TaskStatus(task.status)]:
            transition_task(session, task, TaskStatus.CANCELLED, actor=actor, reason="run cancelled")


def resume(session: Session, run: Run, *, actor: str, decision: str, reason: str) -> None:
    """Human decision on a NEEDS_HUMAN run: resume the stage, replan, or cancel."""
    _require(run, RunStatus.NEEDS_HUMAN)
    if not reason.strip():
        raise DecisionError("record why you resume, replan or cancel")
    record_event(session, "needs_human.decided", actor=actor, acting_role=TL, run_id=run.id,
                 payload={"decision": decision, "reason": reason, "resume_status": run.resume_status})
    if decision == "cancel":
        cancel(session, run, actor=actor, reason=reason)
        return
    if decision == "replan":
        _corrected(run)
        coordinator_replan(session, run, actor=actor, reason=reason)
        return
    if decision in ("amend_tests", "keep_tests"):
        _rule_on_dispute(session, run, actor=actor, decision=decision, reason=reason)
        return
    target = RunStatus(run.resume_status or RunStatus.PREFLIGHT)
    kinds = {RunStatus.PREFLIGHT: "preflight", RunStatus.SPECIFYING: "specify", RunStatus.PLANNING: "plan",
             RunStatus.REVIEWING: "review", RunStatus.PR_OPEN: "release"}
    if target == RunStatus.GROUNDING:
        target = RunStatus.PLANNING
    if target in (RunStatus.CODING, RunStatus.READY):
        transition_run(session, run, RunStatus.CODING, actor=actor, acting_role=TL, reason=reason)
        for task in coordinator.current_tasks(session, run):
            if task.status in (TaskStatus.FAILED, TaskStatus.DISPUTED, TaskStatus.UNKNOWN):
                transition_task(session, task, TaskStatus.READY, actor=actor, reason=reason)
        coordinator.schedule_tasks(session, run)
        return
    if target not in kinds:
        raise DecisionError(f"cannot resume at {target}; replan or cancel instead")
    transition_run(session, run, target, actor=actor, acting_role=TL, reason=reason)
    coordinator.queue(session, run, kinds[target])


def _rule_on_dispute(session: Session, run: Run, *, actor: str, decision: str, reason: str) -> None:
    """The Tech Lead decides a test dispute: the QA Engineer updates the tests, or the tests stand.

    The ruling is stored for the task's next attempt only; the task continues from its committed tests.
    """
    disputed = [t for t in coordinator.current_tasks(session, run) if t.status == TaskStatus.DISPUTED]
    if not disputed:
        raise DecisionError("no task is disputed; resume, replan or cancel instead")
    task = disputed[0]
    if decision == "amend_tests":
        _corrected(run)
    ruling = {"task": task.external_task_id, "decision": decision, "reason": reason, "actor": actor,
              "attempt": task.attempt_count + 1, "dispute": run.status_reason}
    coordinator.store_artifact(session, run, f"ruling:{task.external_task_id}", TeamRole.COORDINATOR, json.dumps(ruling))
    record_event(session, "dispute.ruled", actor=actor, acting_role=TL, run_id=run.id, payload=ruling)
    transition_run(session, run, RunStatus.CODING, actor=actor, acting_role=TL, reason=reason)
    transition_task(session, task, TaskStatus.READY, actor=actor, reason=f"{decision}: {reason}")
    coordinator.schedule_tasks(session, run)


# ---------------------------------------------------------------- Code Owner

def pr_decision(session: Session, run: Run, *, actor: str, decision: str, reason: str | None) -> None:
    _require(run, RunStatus.WAITING_PR_APPROVAL)
    pull = session.scalar(select(PullRequest).where(PullRequest.run_id == run.id))
    _approval(session, run, "pr", decision, actor, CO, reason=reason)
    if decision == "approve":
        if not pull.simulated:
            raise DecisionError("merge the PR in GitHub; the platform records the merge from GitHub")
        pull.merged_sha, pull.review_status = pull.head_sha, "approved"
        transition_run(session, run, RunStatus.COMPLETED, actor=actor, acting_role=CO,
                       reason="simulated merge recorded (B2 has no real pull request)")
    elif decision == "request_changes":
        if not (reason or "").strip():
            raise DecisionError("say which changes the team should make")
        _corrected(run)
        pull.review_status = "changes_requested"
        transition_run(session, run, RunStatus.CODING, actor=actor, acting_role=CO, reason=reason)
        for task in coordinator.current_tasks(session, run):
            transition_task(session, task, TaskStatus.READY, actor=actor, reason=f"changes requested: {reason}")
        coordinator.schedule_tasks(session, run)
    elif decision == "rereview":
        # The code is unchanged; the Code Reviewer writes a new review.md and the PR body is refreshed.
        transition_run(session, run, RunStatus.REVIEWING, actor=actor, acting_role=CO,
                       reason=reason or "Code Owner asked for a new review")
        coordinator.queue(session, run, "review", key=f"rereview:v{run.version}")
    elif decision == "close":
        pull.review_status = "closed"
        transition_run(session, run, RunStatus.CLOSED_UNMERGED, actor=actor, acting_role=CO, reason=reason)
    else:
        raise DecisionError(f"unknown decision {decision}")


# ---------------------------------------------------------------- classification

def needs_classification(run: Run) -> bool:
    finished_badly = run.status in (RunStatus.FAILED, RunStatus.NEEDS_HUMAN, RunStatus.CLOSED_UNMERGED)
    corrected_and_done = run.human_corrected and run.status in TERMINAL
    return (finished_badly or corrected_and_done) and run.failure_category is None


def classify(session: Session, run: Run, *, actor: str, category: str, stage: str, role: str, note: str) -> None:
    if category not in FAILURE_CATEGORIES or stage not in FAILURE_STAGES or role not in FAILURE_ROLES:
        raise DecisionError("unknown category, stage or role")
    if category == "OTHER" and not note.strip():
        raise DecisionError("OTHER needs a note")
    run.failure_category, run.failure_stage, run.failure_role, run.failure_note = category, stage, role, note
    record_event(session, "failure.classified", actor=actor, acting_role=TL, run_id=run.id,
                 payload={"category": category, "stage": stage, "role": role, "note": note})
