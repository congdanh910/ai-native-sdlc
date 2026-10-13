"""JSON shapes returned to the web control room."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from controller.app.models import (Approval, Clarification, Document, Event, Execution, Plan, Project, PullRequest,
                                   Run, Task)
from controller.app.services import needs_classification
from controller.app.states import ACTIVE, STAGE_STARTER, WAITING_FOR


def project_view(project: Project) -> dict:
    return {"id": str(project.id), "key": project.key, "repo_url": project.repo_url,
            "default_branch": project.default_branch, "readiness": project.readiness,
            "report": project.readiness_report.get("items", []), "checked_commit": project.checked_commit,
            "updated_at": project.updated_at.isoformat() if project.updated_at else None}


def waiting(run: Run) -> dict | None:
    if run.awaiting_start:
        return {"role": STAGE_STARTER[run.awaiting_start].value, "action": "start", "stage": run.awaiting_start}
    role = WAITING_FOR.get(run.status)
    if role:
        return {"role": role.value, "action": run.status.lower()}
    if needs_classification(run):
        return {"role": "tech_lead", "action": "classify"}
    return None


def run_summary(run: Run, project_key: str) -> dict:
    return {"id": str(run.id), "title": run.title, "project": project_key, "status": run.status,
            "version": run.version, "reason": run.status_reason, "waiting": waiting(run),
            "experiment": run.experiment, "updated_at": run.updated_at.isoformat(),
            "created_at": run.created_at.isoformat()}


def _document(document: Document) -> dict:
    return {"id": str(document.id), "kind": document.kind, "version": document.version, "sha256": document.sha256,
            "author_kind": document.author_kind, "author": document.author, "content": document.content,
            "repo_path": document.repo_path, "commit_sha": document.commit_sha,
            "created_at": document.created_at.isoformat()}


def _plan(plan: Plan) -> dict:
    return {"id": str(plan.id), "version": plan.version, "sha256": plan.sha256, "author_kind": plan.author_kind,
            "author": plan.author, "plan": plan.payload, "test_plan": plan.test_plan,
            "grounding": plan.grounding_report, "commit_sha": plan.commit_sha, "created_at": plan.created_at.isoformat()}


def run_detail(session: Session, run: Run) -> dict:
    project = session.get(Project, run.project_id)
    documents = session.scalars(select(Document).where(Document.run_id == run.id)
                                .order_by(Document.kind, Document.version)).all()
    plans = session.scalars(select(Plan).where(Plan.run_id == run.id).order_by(Plan.version)).all()
    latest_plan_id = plans[-1].id if plans else None
    tasks = session.scalars(select(Task).where(Task.run_id == run.id, Task.plan_id == latest_plan_id)
                            .order_by(Task.external_task_id)).all() if plans else []
    pull = session.scalar(select(PullRequest).where(PullRequest.run_id == run.id))
    executions = session.scalars(select(Execution).where(Execution.run_id == run.id)
                                 .order_by(Execution.created_at)).all()
    detail = run_summary(run, project.key)
    detail.update({
        "base_commit": run.base_commit, "branch": run.branch, "stage_modes": run.stage_modes,
        "commit_artifacts": run.commit_artifacts, "human_corrected": run.human_corrected,
        "failure": {"category": run.failure_category, "stage": run.failure_stage, "role": run.failure_role,
                    "note": run.failure_note},
        "documents": [_document(d) for d in documents],
        "plans": [_plan(p) for p in plans],
        "tasks": [{"id": t.external_task_id, "title": t.payload["title"], "status": t.status,
                   "attempts": t.attempt_count, "depends_on": t.payload.get("depends_on", [])} for t in tasks],
        "clarifications": [{"id": str(c.id), "question": c.question, "blocking": c.blocking, "answer": c.answer}
                           for c in session.scalars(select(Clarification).where(Clarification.run_id == run.id)
                                                    .order_by(Clarification.created_at))],
        "approvals": [{"stage": a.stage, "decision": a.decision, "actor": a.actor, "role": a.acting_role,
                       "reason": a.reason, "created_at": a.created_at.isoformat()}
                      for a in session.scalars(select(Approval).where(Approval.run_id == run.id)
                                               .order_by(Approval.created_at))],
        "pull_request": None if pull is None else {
            "url": pull.url, "number": pull.number, "head_sha": pull.head_sha, "ci_status": pull.ci_status,
            "review_status": pull.review_status, "merged_sha": pull.merged_sha, "simulated": pull.simulated},
        "executions": [{"kind": e.kind, "role": e.role, "status": e.status, "attempts": e.attempts,
                        "engine": e.engine, "error": e.error} for e in executions],
    })
    return detail


def events_view(session: Session, run_id) -> list[dict]:
    return [{"id": e.id, "type": e.event_type, "actor": e.actor, "role": e.acting_role, "payload": e.payload,
             "created_at": e.created_at.isoformat()}
            for e in session.scalars(select(Event).where(Event.run_id == run_id).order_by(Event.id))]


def inbox(session: Session) -> list[dict]:
    items = []
    rows = session.execute(select(Run, Project.key).join(Project, Project.id == Run.project_id)
                           .order_by(Run.updated_at)).all()
    for run, key in rows:
        if run.status not in ACTIVE and not needs_classification(run):
            continue
        need = waiting(run)
        if need:
            items.append({**run_summary(run, key), **{"role": need["role"], "action": need["action"],
                                                       "stage": need.get("stage")}})
    return items
