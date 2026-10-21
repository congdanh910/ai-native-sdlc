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
from controller.app.models import Approval, Clarification, Plan, Project, PullRequest, Run, Task
from controller.app.discovery.service import DiscoveryRequest, render_request
from controller.app.foundation import FoundationRequest, foundation_intent
from controller.app.project_docs import (SDS_PATH, SRS_PATH, ArchitectureBrief, ProjectBrief, check_sds_markdown,
                                         check_srs_markdown, render_architecture_brief, render_brief, srs_features)
from controller.app.projects.registry import mirror_for
from controller.app.schemas import Intent, PlanPayload, TestPlan, parse_criteria, render_intent
from controller.app.state_machine import transition_run, transition_task
from controller.app.config import get_settings
from controller.app.states import TASK_TRANSITIONS, TERMINAL, HumanRole, RunKind, RunStatus, TaskStatus, TeamRole

PO, TL, CO = HumanRole.PRODUCT_OWNER.value, HumanRole.TECH_LEAD.value, HumanRole.CODE_OWNER.value
QC = HumanRole.QC.value

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
              reason: str | None = None, document_id=None, plan_id=None, checklist: dict | None = None) -> None:
    session.add(Approval(run_id=run.id, stage=stage, decision=decision, actor=actor, acting_role=role,
                         reason=reason, document_id=document_id, plan_id=plan_id, checklist=checklist or {}))
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

def create_run(session: Session, project: Project,
               intent: Intent | ProjectBrief | ArchitectureBrief | DiscoveryRequest | FoundationRequest,
               *, actor: str,
               idempotency_key: str,
               experiment: bool = False, base_commit: str | None = None, stage_overrides: dict | None = None,
               eval_run_id: uuid.UUID | None = None, eval_case: str | None = None) -> Run:
    existing = session.scalar(select(Run).where(Run.idempotency_key == idempotency_key))
    if existing:
        return existing
    if project.readiness == "not_ready":
        raise DecisionError(f"project {project.key} is not ready; open Projects and run the check")
    kind = (RunKind.SRS if isinstance(intent, ProjectBrief) else RunKind.SDS if isinstance(intent, ArchitectureBrief)
            else RunKind.DISCOVERY if isinstance(intent, DiscoveryRequest)
            else RunKind.FOUNDATION if isinstance(intent, FoundationRequest) else RunKind.FEATURE)
    sds_text = None
    if kind == RunKind.FOUNDATION:
        sds_text = mirror_for(project).read_file(project.checked_commit, SDS_PATH) if project.checked_commit else None
        if not sds_text:
            raise DecisionError(f"{project.key} has no SDS.md on {project.default_branch}; write and merge the SDS "
                                "first, then run the project check")
    if kind == RunKind.DISCOVERY:
        open_discovery = session.scalar(select(Run).where(Run.project_id == project.id, Run.kind == RunKind.DISCOVERY,
                                                          Run.status.not_in([s.value for s in TERMINAL])))
        if open_discovery and open_discovery.idempotency_key != idempotency_key:
            raise DecisionError(f"{project.key} already has an open discovery ({str(open_discovery.id)[:8]}); "
                                "finish or cancel it first")
    if kind != RunKind.FEATURE and experiment:
        raise DecisionError(f"an {kind.upper()} run cannot be an experiment")
    if not experiment:
        _recheck_if_behind(session, project, actor)
    has_srs = any(i.get("name") == SRS_PATH and i.get("status") == "ok" for i in project.readiness_report.get("items", []))
    if kind == RunKind.FEATURE and has_srs and not experiment:
        if not intent.story:
            raise DecisionError(f"{project.key} has an SRS: pick the user story from SRS.md section 7 that this run delivers")
        stories = {s.id for f in srs_features(mirror_for(project).read_file(project.checked_commit, SRS_PATH) or "")
                   for s in f.stories}
        if intent.story not in stories:
            raise DecisionError(f"{intent.story} is not a user story in SRS.md section 7")
    if kind == RunKind.SDS:
        srs_item = next((i for i in project.readiness_report.get("items", []) if i.get("name") == SRS_PATH), None)
        if not srs_item or srs_item.get("status") != "ok":
            raise DecisionError(f"{project.key} has no SRS.md with user stories on {project.default_branch}; "
                                "write and merge the SRS first, then run the project check")
        platform = intent.deployment_context.platform
        if platform:
            from controller.app.projects.config import parse_project_config
            raw = mirror_for(project).read_file(project.checked_commit, ".sdlc/project.yaml") or ""
            allowed = parse_project_config(raw).architecture.hosting_platforms if raw else []
            if allowed and platform not in allowed:
                raise DecisionError(f"hosting platform {platform} is not one of {project.key}'s "
                                    f"architecture.hosting_platforms ({', '.join(allowed)})")
    title = {RunKind.SRS: f"SRS: {intent.title}", RunKind.SDS: f"SDS: {intent.title}",
             RunKind.DISCOVERY: f"Discovery: {intent.title}", RunKind.FOUNDATION: f"Foundation: {intent.title}"}.get(kind, intent.title)
    run = Run(project_id=project.id, title=title, kind=kind.value, idempotency_key=idempotency_key,
              status=RunStatus.RECEIVED.value, experiment=experiment, base_commit=base_commit,
              stage_overrides=stage_overrides or {}, eval_run_id=eval_run_id, eval_case=eval_case)
    session.add(run)
    session.flush()
    if not experiment:  # pinned at creation, so a later .env change never alters a run in flight
        pinned = dict(get_settings().default_engines)
        if kind == RunKind.DISCOVERY and get_settings().sdlc_discovery_engine:
            from controller.app.team.readonly import validate_engines
            pinned["business_analyst"] = validate_engines(
                {"business_analyst": get_settings().sdlc_discovery_engine})["business_analyst"]
        engines = json.dumps(pinned)
        coordinator.store_artifact(session, run, "engines", TeamRole.COORDINATOR, engines)
    render = {RunKind.SRS: render_brief, RunKind.SDS: render_architecture_brief, RunKind.DISCOVERY: render_request,
              RunKind.FOUNDATION: lambda request: foundation_intent(request, sds_text)}.get(kind, render_intent)
    add_document(session, run.id, "intent", render(intent),
                 author_kind="human", author=actor, data=intent.model_dump())
    record_event(session, "intent.accepted", actor=actor, acting_role=PO, run_id=run.id, project_id=project.id,
                 payload={"kind": run.kind})
    transition_run(session, run, RunStatus.PREFLIGHT, actor=actor, acting_role=PO)
    coordinator.queue(session, run, "preflight")
    return run


def _recheck_if_behind(session: Session, project: Project, actor: str) -> None:
    """The project check reads SRS.md and SDS.md on the default branch; after a merge it is out of date until someone
    runs it again. When the branch has moved past the checked commit, run the check now, so a merged SRS counts."""
    from controller.app.projects.registry import check_project
    mirror = mirror_for(project)
    try:
        mirror.fetch()
        head = mirror.resolve(project.default_branch)
    except Exception:  # the check itself reports an unreachable repository
        return
    if head != project.checked_commit:
        check_project(session, project, actor)
        if project.readiness == "not_ready":
            raise DecisionError(f"{project.default_branch} moved to {head[:12]} and the project check now fails; "
                                "open Projects to see why")


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
    spec = latest_document(session, run.id, coordinator.spec_kind(run))
    if spec is None or spec.id != document_id or spec.sha256 != sha256:
        raise DecisionError("this is not the latest spec version; reload and review it again")
    return spec


def gate_role(run: Run) -> str:
    """Who decides at the first gate: the Product Owner, or the Tech Lead for the architecture (SDS)."""
    return TL if run.kind == RunKind.SDS else PO


def _checklist(session: Session, run: Run, checked: list[str] | None) -> dict:
    try:
        return coordinator.require_checklist(session, run, checked)
    except ValueError as exc:
        raise DecisionError(str(exc)) from exc


def approve_spec(session: Session, run: Run, document_id: uuid.UUID, sha256: str, *, actor: str,
                 checked: list[str] | None = None) -> None:
    _require(run, RunStatus.WAITING_SPEC_APPROVAL)
    spec = _current_spec(session, run, document_id, sha256)
    checklist = _checklist(session, run, checked)
    _approval(session, run, "spec", "approve", actor, gate_role(run), document_id=spec.id, checklist=checklist)
    coordinator.accept_spec(session, run, spec, actor=actor, acting_role=gate_role(run))


def revise_spec(session: Session, run: Run, content: str, *, actor: str, reason: str | None) -> None:
    _require(run, RunStatus.WAITING_SPEC_APPROVAL)
    if run.kind in (RunKind.SRS, RunKind.SDS):
        if run.kind == RunKind.SRS:
            problems = check_srs_markdown(content)
        else:
            srs = mirror_for(session.get(Project, run.project_id)).read_file(run.base_commit, SRS_PATH)
            problems = check_sds_markdown(content, srs)
        if problems:
            raise DecisionError("; ".join(problems))
    else:
        try:
            parse_criteria(content)
        except ValueError as exc:
            raise DecisionError(str(exc)) from exc
    document = add_document(session, run.id, coordinator.spec_kind(run), content, author_kind="human", author=actor)
    _approval(session, run, "spec", "revise", actor, gate_role(run), reason=reason, document_id=document.id)
    _corrected(run)


def reject_spec(session: Session, run: Run, *, actor: str, reason: str, next_step: str) -> None:
    _require(run, RunStatus.WAITING_SPEC_APPROVAL)
    if not reason.strip():
        raise DecisionError("a rejection needs a reason")
    _approval(session, run, "spec", "reject", actor, gate_role(run), reason=reason)
    _corrected(run)
    if next_step == "cancel":
        transition_run(session, run, RunStatus.CANCELLED, actor=actor, acting_role=gate_role(run), reason=reason)
    else:
        transition_run(session, run, RunStatus.SPECIFYING, actor=actor, acting_role=gate_role(run), reason=reason)
        coordinator.queue(session, run, "specify")


# ---------------------------------------------------------------- Tech Lead

def _current_plan(session: Session, run: Run, plan_id: uuid.UUID, sha256: str):
    plan = latest_plan(session, run.id)
    if plan is None or plan.id != plan_id or plan.sha256 != sha256:
        raise DecisionError("this is not the latest plan version; reload and review it again")
    return plan


def approve_plan(session: Session, run: Run, plan_id: uuid.UUID, sha256: str, *, actor: str,
                 checked: list[str] | None = None) -> None:
    _require(run, RunStatus.WAITING_PLAN_APPROVAL)
    plan = _current_plan(session, run, plan_id, sha256)
    checklist = _checklist(session, run, checked)
    _approval(session, run, "plan", "approve", actor, TL, plan_id=plan.id, checklist=checklist)
    plan.commit_sha = coordinator.commit_thread(session, run, "plan", coordinator.plan_markdown(run, plan),
                                                f"sdlc: plan v{plan.version} approved")
    coordinator.enter_quality_gate(session, run, plan, actor=actor, acting_role=TL)


# ---------------------------------------------------------------- QC (Quality gate)

def _current_tests(session: Session, run: Run, document_id: uuid.UUID, sha256: str):
    document = latest_document(session, run.id, "tests")
    if document is None or document.id != document_id or document.sha256 != sha256:
        raise DecisionError("these are not the latest test cases; reload and review them again")
    return document


def _approved_payload(session: Session, run: Run) -> dict:
    """The plan payload the Tech Lead approved; a test change at the Quality gate must keep it."""
    approval = session.scalar(select(Approval).where(Approval.run_id == run.id, Approval.stage == "plan",
                                                     Approval.decision == "approve").order_by(Approval.created_at.desc()))
    return session.get(Plan, approval.plan_id).payload


def approve_tests(session: Session, run: Run, document_id: uuid.UUID, sha256: str, *, actor: str,
                  checked: list[str] | None = None) -> None:
    _require(run, RunStatus.WAITING_TEST_APPROVAL)
    document = _current_tests(session, run, document_id, sha256)
    plan = latest_plan(session, run.id)
    if document.data.get("plan_id") != str(plan.id) or plan.payload != _approved_payload(session, run):
        raise DecisionError("the test cases do not belong to the approved plan; reload the run")
    checklist = _checklist(session, run, checked)
    _approval(session, run, "tests", "approve", actor, QC, document_id=document.id, plan_id=plan.id,
              checklist=checklist)
    document.commit_sha = coordinator.commit_thread(session, run, "tests", document.content,
                                                    f"sdlc: test cases v{document.version} approved")
    coordinator.record_analysis(session, run, "before")
    plan.commit_sha = coordinator.write_plan_files(session, run, plan) or plan.commit_sha
    for task in PlanPayload.model_validate(plan.payload).tasks:
        session.add(Task(run_id=run.id, plan_id=plan.id, external_task_id=task.id, payload=task.model_dump(),
                         status=TaskStatus.PENDING.value))
    session.flush()
    transition_run(session, run, RunStatus.READY, actor=actor, acting_role=QC)
    project = session.get(Project, run.project_id)
    if project.readiness != "ready" and not get_settings().coding_is_mocked:
        coordinator.to_needs_human(session, run, f"project {project.key} is {project.readiness}; coding needs ready")
        return
    coordinator.enter_stage(session, run, "coding", actor=actor, acting_role=QC)


def revise_tests(session: Session, run: Run, test_plan: dict, *, actor: str, reason: str | None) -> None:
    """QC edits the test plan; the plan stays as approved."""
    _require(run, RunStatus.WAITING_TEST_APPROVAL)
    try:
        tests = TestPlan.model_validate(test_plan)
    except ValidationError as exc:
        error = exc.errors()[0]
        raise DecisionError(f"{'.'.join(str(p) for p in error['loc'])}: {error['msg']}") from exc
    payload = _approved_payload(session, run)
    known = {t["id"] for t in payload["tasks"]}
    unknown = sorted({c.task_id for c in tests.cases} - known)
    if unknown:
        raise DecisionError(f"test cases name tasks {', '.join(unknown)} that the approved plan does not have")
    plan = add_plan(session, run.id, payload, tests.model_dump(), author_kind="human", author=actor)
    coordinator.ground(session, run, plan)
    document = coordinator.add_tests_document(session, run, plan)
    _approval(session, run, "tests", "revise", actor, QC, reason=reason, document_id=document.id, plan_id=plan.id)
    _corrected(run)


def reject_tests(session: Session, run: Run, *, actor: str, reason: str, next_step: str) -> None:
    """redo: the QA Analyst designs the tests again; replan: back to the Tech Lead; cancel: stop the run."""
    _require(run, RunStatus.WAITING_TEST_APPROVAL)
    if not reason.strip():
        raise DecisionError("a rejection needs a reason")
    _approval(session, run, "tests", "reject", actor, QC, reason=reason)
    _corrected(run)
    if next_step == "cancel":
        transition_run(session, run, RunStatus.CANCELLED, actor=actor, acting_role=QC, reason=reason)
    elif next_step == "replan":
        transition_run(session, run, RunStatus.PLANNING, actor=actor, acting_role=QC, reason=reason)
        coordinator.queue(session, run, "plan")
    else:
        transition_run(session, run, RunStatus.DESIGNING_TESTS, actor=actor, acting_role=QC, reason=reason)
        coordinator.queue(session, run, "design_tests")


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
             RunStatus.DESIGNING_OPTIONS: "options", RunStatus.DESIGNING_ONBOARDING: "onboarding",
             RunStatus.PREPARING_ENVIRONMENT: "environment",
             RunStatus.DESIGNING_TESTS: "design_tests",
             RunStatus.REVIEWING: "review", RunStatus.PR_OPEN: "release", RunStatus.EXTRACTING: "extract"}
    if target == RunStatus.INGESTING:  # a discovery waiting for sources has no job to queue
        transition_run(session, run, target, actor=actor, acting_role=TL, reason=reason)
        return
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


def github_event(session: Session, delivery: str, event: str) -> str:
    """A verified GitHub webhook. Each delivery is handled once; handling re-reads PR state, so it is idempotent."""
    from controller.app.doing.github import PR_EVENTS
    from controller.app.models import Event
    seen = session.scalar(select(Event.id).where(Event.event_type == "github.delivery",
                                                 Event.payload["delivery"].astext == delivery).limit(1))
    if seen is not None:
        return "duplicate"
    record_event(session, "github.delivery", actor="github", payload={"delivery": delivery, "event": event})
    if event not in PR_EVENTS:
        return "ignored"
    coordinator.poll_pull_requests(session)
    return "processed"


# ---------------------------------------------------------------- Code Owner

def pr_decision(session: Session, run: Run, *, actor: str, decision: str, reason: str | None,
                checked: list[str] | None = None) -> None:
    """approve (simulated PRs), checklist (record the review before merging in GitHub), request_changes,
    rereview or close."""
    _require(run, RunStatus.WAITING_PR_APPROVAL)
    pull = session.scalar(select(PullRequest).where(PullRequest.run_id == run.id))
    checklist = _checklist(session, run, checked) if decision in ("approve", "checklist") else None
    _approval(session, run, "pr", decision, actor, CO, reason=reason, checklist=checklist)
    if decision == "checklist":
        return
    if decision == "approve":
        if not pull.simulated:
            raise DecisionError("merge the PR in GitHub; the platform records the merge from GitHub")
        pull.merged_sha, pull.review_status = pull.head_sha, "approved"
        transition_run(session, run, RunStatus.COMPLETED, actor=actor, acting_role=CO,
                       reason="simulated merge recorded (B2 has no real pull request)")
        coordinator.recheck_after_merge(session, session.get(Project, run.project_id))
    elif decision == "request_changes":
        if not (reason or "").strip():
            raise DecisionError("say which changes the team should make")
        _corrected(run)
        pull.review_status = "changes_requested"
        if run.kind in (RunKind.SRS, RunKind.SDS):
            # The document is written again with the reason as feedback, goes through its gate again, and the
            # same draft PR gets the new version.
            transition_run(session, run, RunStatus.SPECIFYING, actor=actor, acting_role=CO, reason=reason)
            coordinator.queue(session, run, "specify", key=f"pr-changes:v{run.version}")
            return
        if run.kind == RunKind.DISCOVERY:
            # Back to clarifying; the next approved baseline updates the same draft PR.
            transition_run(session, run, RunStatus.CLARIFYING, actor=actor, acting_role=CO, reason=reason)
            return
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


# ---------------------------------------------------------------- SDS: architecture options

SA = HumanRole.SOLUTION_ARCHITECT.value


def choose_option(session: Session, run: Run, document_id: uuid.UUID, option: str, *, actor: str, reason: str,
                  checked: list[str] | None, adjustments: str = "", hosting: str = "", hosting_reason: str = "") -> None:
    """The Solution Architect picks the option the SDS designs and how it is hosted (one of its hosting variants),
    with adjustments to it if any (another database, an added queue); the reason and the adjustments go to SDS
    section 9."""
    from controller.app.project_docs import stored_options
    _require(run, RunStatus.WAITING_OPTIONS_APPROVAL)
    document = latest_document(session, run.id, "options")
    if document is None or document.id != document_id:
        raise DecisionError("the options changed; reload the page")
    if not reason.strip():
        raise DecisionError("say why this option was chosen; it goes into SDS section 9")
    options = stored_options(document.data)
    chosen = next((o for o in options.options if o.id == option), None)
    if chosen is None:
        raise DecisionError(f"{option} is not one of the options")
    variant = None
    if chosen.hosting:
        assumed = document.data.get("hosting_basis") == "assumed"
        variant = next((v for v in chosen.hosting if v.id == (hosting or ("" if assumed else chosen.recommended_hosting))), None)
        if variant is None:
            raise DecisionError(f"pick one of the hosting variants of {chosen.id}")
        if assumed and not hosting_reason.strip():
            raise DecisionError("the brief does not state the deployment environment: say why this hosting "
                                "(contracts, data residency, the team that runs it); it goes into SDS section 9")
    checklist = _checklist(session, run, checked)
    _approval(session, run, "options", "approve", actor, SA, reason=f"{chosen.id} {chosen.name}"
              + (f", hosted as {variant.id} {variant.name}" if variant else "") + f": {reason}"
              + (f" | hosting: {hosting_reason.strip()}" if hosting_reason.strip() else "")
              + (f" | adjustments: {adjustments.strip()}" if adjustments.strip() else ""),
              document_id=document.id, checklist=checklist)
    coordinator.store_artifact(session, run, "chosen_option", TeamRole.COORDINATOR, json.dumps({
        **chosen.model_dump(exclude={"hosting"}), "reason": reason.strip(), "chosen_by": actor,
        "adjustments": adjustments.strip(), "hosting": variant.model_dump() if variant else None,
        "hosting_reason": hosting_reason.strip(),
        "other_hosting": [{"id": v.id, "name": v.name} for v in chosen.hosting if variant and v.id != variant.id],
        "others": [{"id": o.id, "name": o.name} for o in options.options if o.id != chosen.id]}))
    coordinator.enter_stage(session, run, "spec", actor=actor, acting_role=SA)


def approve_onboarding(session: Session, run: Run, document_id: uuid.UUID, *, actor: str,
                       checked: list[str] | None) -> None:
    """The Tech Lead accepts the onboarding files: the controller commits them to the run's branch (the coding roles
    may never touch them), then the new validation environment is built and tried."""
    _require(run, RunStatus.WAITING_ONBOARDING_APPROVAL)
    document = latest_document(session, run.id, "onboarding")
    if document is None or document.id != document_id:
        raise DecisionError("the onboarding changed; reload the page")
    _approval(session, run, "onboarding", "approve", actor, TL, document_id=document.id,
              checklist=_checklist(session, run, checked))
    project = session.get(Project, run.project_id)
    commit = coordinator.worktree_for(project, run).commit_files(
        document.data["files"], f"sdlc: onboarding v{document.version} for the stack of SDS.md")
    coordinator.store_artifact(session, run, "onboarding_commit", TeamRole.COORDINATOR, commit)
    transition_run(session, run, RunStatus.PREPARING_ENVIRONMENT, actor=actor, acting_role=TL,
                   reason=f"onboarding v{document.version} committed as {commit[:12]}")
    coordinator.queue(session, run, "environment")


def decide_acceptance(session: Session, run: Run, *, actor: str, decision: str, reason: str = "",
                      document_id: uuid.UUID | None = None, checked: list[str] | None = None) -> None:
    """The QC's acceptance gate. accept: every criterion passed on the preview, the PR opens. reject: defects go to
    the Developer (every task again with the QC's notes), then review and acceptance again. retest: run the tests
    again, rewritten by the QA Engineer when the reason says what is wrong with them."""
    _require(run, RunStatus.WAITING_ACCEPTANCE_APPROVAL)
    if decision == "accept":
        document = latest_document(session, run.id, "acceptance")
        if document is None or document.id != document_id:
            raise DecisionError("the acceptance report changed; reload the page")
        _approval(session, run, "acceptance", "approve", actor, QC, document_id=document.id,
                  checklist=_checklist(session, run, checked))
        transition_run(session, run, RunStatus.PR_OPEN, actor=actor, acting_role=QC, reason="the QC accepted the story")
        coordinator.queue(session, run, "release")
    elif decision == "reject":
        if not reason.strip():
            raise DecisionError("describe the defects: what you did, what you saw, what you expected")
        _corrected(run)
        _approval(session, run, "acceptance", "reject", actor, QC, reason=reason)
        transition_run(session, run, RunStatus.CODING, actor=actor, acting_role=QC, reason=reason)
        for task in coordinator.current_tasks(session, run):
            transition_task(session, task, TaskStatus.READY, actor=actor, reason=f"defects found in acceptance: {reason}")
        coordinator.schedule_tasks(session, run)
    elif decision == "retest":
        _approval(session, run, "acceptance", "retest", actor, QC, reason=reason or None)
        transition_run(session, run, RunStatus.ACCEPTANCE_TESTING, actor=actor, acting_role=QC,
                       reason=reason or "the QC asked to run the acceptance tests again")
        coordinator.queue(session, run, "acceptance", key=f"retest:v{run.version}")
    else:
        raise DecisionError(f"unknown acceptance decision {decision!r}")


def preview(session: Session, run: Run, *, actor: str, start: bool) -> None:
    """Start or stop the run's preview: the application of its branch for people to try (127.0.0.1)."""
    from controller.app.projects.config import EffectivePolicy
    raw = coordinator.artifact(session, run.id, "effective_policy")
    if raw is None or EffectivePolicy.model_validate_json(raw).preview is None:
        raise DecisionError("the project has no preview in .sdlc/project.yaml; the foundation run sets it up")
    if start and not run.worktree_path:
        raise DecisionError("the run has no branch to preview yet")
    if start:
        from pathlib import Path
        from controller.app.preview import missing_parts
        missing = missing_parts(Path(run.worktree_path), EffectivePolicy.model_validate_json(raw).preview)
        if missing:
            raise DecisionError(f"nothing to preview yet: {', '.join(missing)} not in the run's branch; "
                                "the run's coding creates them")
    record_event(session, f"preview.{'requested' if start else 'stop_requested'}", actor=actor, run_id=run.id, payload={})
    coordinator.queue_preview(session, run, start=start, reason=f"requested by {actor}")


def redo_onboarding(session: Session, run: Run, *, actor: str, reason: str) -> None:
    _require(run, RunStatus.WAITING_ONBOARDING_APPROVAL)
    if not reason.strip():
        raise DecisionError("say what the onboarding files must change")
    _approval(session, run, "onboarding", "reject", actor, TL, reason=reason)
    transition_run(session, run, RunStatus.DESIGNING_ONBOARDING, actor=actor, acting_role=TL, reason=reason)
    coordinator.queue(session, run, "onboarding", key=f"redo:v{run.version}")


def redo_options(session: Session, run: Run, *, actor: str, reason: str) -> None:
    _require(run, RunStatus.WAITING_OPTIONS_APPROVAL)
    if not reason.strip():
        raise DecisionError("say what the comparison misses")
    _approval(session, run, "options", "reject", actor, SA, reason=reason)
    transition_run(session, run, RunStatus.DESIGNING_OPTIONS, actor=actor, acting_role=SA, reason=reason)
    coordinator.queue(session, run, "options", key=f"redo:v{run.version}")


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
