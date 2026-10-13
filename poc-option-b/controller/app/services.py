import hashlib
import hmac
import json
import re
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from controller.app.models import Approval, Artifact, Event, Execution, Plan, PullRequest, Run, Task, TaskDependency
from controller.app.schemas import FailureClassification, PlanDecision, PlanPayload, PlanRevision, RunCreate, VersionedDecision
from controller.app.state_machine import ConflictError, NotFoundError, event, transition, utc_now


def get_run(session: Session, run_id: UUID) -> Run:
    run = session.get(Run, run_id)
    if run is None:
        raise NotFoundError("run not found")
    return run


def create_run(session: Session, request: RunCreate, allowed_repos: frozenset[str]) -> Run:
    if request.repo_id not in allowed_repos:
        raise ConflictError("repository is not allowlisted")
    existing = session.scalar(select(Run).where(Run.idempotency_key == request.idempotency_key))
    if existing is not None:
        if (existing.repo_id, existing.base_commit, existing.requirement, existing.executor) != (
            request.repo_id, request.base_commit.lower(), request.requirement, request.executor
        ):
            raise ConflictError("idempotency key already used for different input")
        return existing
    run = Run(
        id=uuid4(),
        idempotency_key=request.idempotency_key,
        repo_id=request.repo_id,
        base_commit=request.base_commit.lower(),
        requirement=request.requirement,
        executor=request.executor,
        status="RECEIVED",
        version=0,
    )
    try:
        with session.begin_nested():
            session.add(run)
            session.flush()
    except IntegrityError:
        existing = session.scalar(select(Run).where(Run.idempotency_key == request.idempotency_key))
        if existing is None:
            raise
        if (existing.repo_id, existing.base_commit, existing.requirement, existing.executor) != (
            request.repo_id, request.base_commit.lower(), request.requirement, request.executor
        ):
            raise ConflictError("idempotency key already used for different input")
        return existing
    event(session, run.id, "run.created", {"repo_id": run.repo_id, "base_commit": run.base_commit})
    return run


def record_plan(session: Session, run_id: UUID, payload: PlanPayload) -> Plan:
    run = get_run(session, run_id)
    if run.status != "PLANNING":
        raise ConflictError("run is not planning")
    body = payload.model_dump(mode="json")
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
    version = (session.scalar(select(func.max(Plan.version)).where(Plan.run_id == run_id)) or 0) + 1
    plan = Plan(id=uuid4(), run_id=run_id, version=version, body=body, sha256=hashlib.sha256(canonical).hexdigest())
    session.add(plan)
    session.flush()
    transition(session, run, "WAITING_PLAN_APPROVAL")
    event(session, run.id, "plan.recorded", {"plan_id": str(plan.id), "version": version, "sha256": plan.sha256})
    return plan


def revise_plan(session: Session, run_id: UUID, revision: PlanRevision) -> Plan:
    """Append a reviewed plan version while leaving execution behind human approval."""
    run = get_run(session, run_id)
    if run.status != "WAITING_PLAN_APPROVAL" or run.version != revision.expected_run_version:
        raise ConflictError("run is not waiting for this plan revision")
    previous = session.scalar(
        select(Plan).where(Plan.run_id == run_id).order_by(Plan.version.desc()).limit(1)
    )
    if previous is None:
        raise ConflictError("run has no plan to revise")
    body = revision.plan.model_dump(mode="json")
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
    checksum = hashlib.sha256(canonical).hexdigest()
    if hmac.compare_digest(previous.sha256, checksum):
        raise ConflictError("revised plan is identical to current plan")
    changed = session.execute(
        update(Run)
        .where(Run.id == run_id, Run.status == "WAITING_PLAN_APPROVAL", Run.version == revision.expected_run_version)
        .values(version=Run.version + 1, updated_at=utc_now())
        .execution_options(synchronize_session=False)
    ).rowcount
    if changed != 1:
        raise ConflictError("run changed concurrently")
    session.expire(run)
    session.refresh(run)
    plan = Plan(
        id=uuid4(), run_id=run_id, version=previous.version + 1,
        body=body, sha256=checksum,
    )
    session.add(plan)
    session.flush()
    event(session, run_id, "plan.revised", {
        "previous_plan_id": str(previous.id), "plan_id": str(plan.id),
        "version": plan.version, "sha256": checksum,
        "actor": revision.actor, "reason": revision.reason,
    })
    return plan


def _checked_plan(session: Session, run: Run, decision: PlanDecision) -> Plan:
    if run.status != "WAITING_PLAN_APPROVAL" or run.version != decision.expected_run_version:
        raise ConflictError("run is not waiting for this plan decision")
    plan = session.get(Plan, decision.plan_id)
    latest = session.scalar(select(func.max(Plan.version)).where(Plan.run_id == run.id))
    if plan is None or plan.run_id != run.id or plan.version != latest:
        raise ConflictError("plan is not the current plan")
    if not hmac.compare_digest(plan.sha256, decision.plan_sha256):
        raise ConflictError("plan checksum mismatch")
    return plan


def approve_plan(session: Session, run_id: UUID, decision: PlanDecision) -> Run:
    run = get_run(session, run_id)
    plan = _checked_plan(session, run, decision)
    payload = PlanPayload.model_validate(plan.body)
    transition(session, run, "READY", decision.expected_run_version)
    session.add(
        Approval(
            id=uuid4(), run_id=run.id, plan_id=plan.id, stage="plan",
            expected_run_version=decision.expected_run_version,
            actor=decision.actor, decision="approved", reason=decision.reason,
        )
    )
    tasks = {
        item.id: Task(
            id=uuid4(), run_id=run.id, plan_id=plan.id, external_task_id=item.id,
            payload_jsonb=item.model_dump(mode="json"),
            status="BLOCKED" if item.dependencies else "QUEUED", attempt_count=0,
            executor=run.executor,
        )
        for item in payload.tasks
    }
    session.add_all(tasks.values())
    session.flush()
    for item in payload.tasks:
        for parent_id in item.dependencies:
            session.add(TaskDependency(task_id=tasks[item.id].id, depends_on_task_id=tasks[parent_id].id))
        if not item.dependencies:
            session.add(
                Execution(
                    id=uuid4(), task_id=tasks[item.id].id,
                    idempotency_key=f"{tasks[item.id].id}:0", status="QUEUED",
                )
            )
    event(session, run.id, "plan.approved", {"plan_id": str(plan.id), "actor": decision.actor})
    return run


def reject_plan(session: Session, run_id: UUID, decision: PlanDecision) -> Run:
    run = get_run(session, run_id)
    plan = _checked_plan(session, run, decision)
    transition(session, run, "NEEDS_HUMAN", decision.expected_run_version)
    session.add(
        Approval(
            id=uuid4(), run_id=run.id, plan_id=plan.id, stage="plan",
            expected_run_version=decision.expected_run_version,
            actor=decision.actor, decision="rejected", reason=decision.reason,
        )
    )
    event(session, run.id, "plan.rejected", {"plan_id": str(plan.id), "actor": decision.actor})
    return run


def retry_failed_review(session: Session, run_id: UUID, decision: VersionedDecision) -> Run:
    """One audited retry for a review infrastructure failure; coding evidence stays fixed."""
    run = get_run(session, run_id)
    if (run.status != "FAILED" or run.version != decision.expected_run_version
            or run.failure_stage != "review" or run.failure_category != "INFRASTRUCTURE"):
        raise ConflictError("run is not eligible for review infrastructure retry")
    retries = session.scalar(
        select(func.count()).select_from(Event).where(
            Event.run_id == run_id, Event.event_type == "review.retry_requested",
        )
    )
    if retries:
        raise ConflictError("review infrastructure retry limit reached")
    tasks = session.scalars(select(Task).where(Task.run_id == run_id)).all()
    if not tasks or any(task.status != "DONE" for task in tasks):
        raise ConflictError("coding evidence is incomplete")
    if session.scalar(select(Artifact.id).where(Artifact.run_id == run_id, Artifact.kind == "review")):
        raise ConflictError("review result already exists")
    prior_failure = run.failure_note
    transition(session, run, "VALIDATING", decision.expected_run_version)
    run.failure_category = None
    run.failure_stage = None
    run.failure_note = None
    event(session, run_id, "review.retry_requested", {
        "actor": decision.actor, "reason": decision.reason,
        "prior_failure": prior_failure,
    })
    return run


def approve_pr(session: Session, run_id: UUID, decision: VersionedDecision) -> Run:
    run = get_run(session, run_id)
    if run.status != "WAITING_PR_APPROVAL":
        raise ConflictError("run is not waiting for PR approval")
    pull_request = session.scalar(select(PullRequest).where(PullRequest.run_id == run_id))
    if pull_request is None or pull_request.ci_status != "success":
        raise ConflictError("required CI check has not passed")
    transition(session, run, "COMPLETED", decision.expected_run_version)
    session.add(
        Approval(
            id=uuid4(), run_id=run.id, stage="pr",
            expected_run_version=decision.expected_run_version,
            actor=decision.actor, decision="approved", reason=decision.reason,
        )
    )
    event(session, run.id, "pr.approved", {"actor": decision.actor})
    return run


def record_draft_pr(
    session: Session, run_id: UUID, *, repo_full_name: str,
    number: int, url: str, head_sha: str,
) -> PullRequest:
    run = get_run(session, run_id)
    if run.status != "PR_READY" or run.repo_id != repo_full_name:
        raise ConflictError("run is not ready for this repository PR")
    expected_path = f"/{repo_full_name}/pull/{number}"
    parsed = urlsplit(url)
    if (
        number < 1 or not re.fullmatch(r"[0-9a-f]{40}([0-9a-f]{24})?", head_sha)
        or parsed.scheme != "https" or parsed.hostname != "github.com"
        or parsed.path != expected_path or parsed.query or parsed.fragment
        or parsed.username or parsed.password or parsed.port
    ):
        raise ConflictError("draft PR identity is invalid")
    if session.scalar(select(PullRequest).where(PullRequest.run_id == run_id)) is not None:
        raise ConflictError("run already has a draft PR")
    pull_request = PullRequest(
        id=uuid4(), run_id=run_id, repo_full_name=repo_full_name,
        number=number, url=url, head_sha=head_sha, ci_status="pending",
    )
    session.add(pull_request)
    transition(session, run, "WAITING_PR_APPROVAL")
    event(session, run_id, "pr.draft_recorded", {
        "repo_full_name": repo_full_name, "number": number, "head_sha": head_sha,
    })
    return pull_request


def cancel_run(session: Session, run_id: UUID, decision: VersionedDecision) -> Run:
    run = get_run(session, run_id)
    if run.status == "CANCELLING":
        raise ConflictError("cancellation already requested")
    if run.status == "CODING":
        transition(session, run, "CANCELLING", decision.expected_run_version)
    else:
        transition(session, run, "CANCELLED", decision.expected_run_version)
    queued = session.scalars(
        select(Execution)
        .join(Task, Execution.task_id == Task.id)
        .where(Task.run_id == run.id, Execution.status == "QUEUED")
    ).all()
    for execution in queued:
        execution.status = "CANCELLED"
        session.get(Task, execution.task_id).status = "CANCELLED"
    for task in session.scalars(select(Task).where(Task.run_id == run.id, Task.status == "BLOCKED")):
        task.status = "CANCELLED"
    event(session, run.id, "run.cancel_requested" if run.status == "CANCELLING" else "run.cancelled", {
        "actor": decision.actor, "reason": decision.reason,
    })
    return run


def classify_failure(session: Session, run_id: UUID, classification: FailureClassification) -> Run:
    run = get_run(session, run_id)
    if run.status not in {"FAILED", "NEEDS_HUMAN"}:
        raise ConflictError("run is not awaiting failure classification")
    run.failure_category = classification.category
    run.failure_stage = classification.stage
    run.failure_note = classification.note
    event(
        session, run.id, "failure.classified",
        {
            "category": classification.category,
            "stage": classification.stage,
            "note": classification.note,
            "corrective_action": classification.corrective_action,
            "actor": classification.actor,
        },
    )
    return run
