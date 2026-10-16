"""JSON shapes returned to the web control room."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from controller.app.models import (Approval, Clarification, Document, Event, Execution, Plan, Project, PullRequest,
                                   Run, Source, Task)
from controller.app.project_docs import SRS_PATH, srs_features
from controller.app.projects.registry import mirror_for
from controller.app.coordinator import evaluate_gate
from controller.app.services import needs_classification
from controller.app.states import ACTIVE, STAGE_STARTER, WAITING_FOR, HumanRole


WAITING_GATES = ("WAITING_BASELINE_APPROVAL", "WAITING_SPEC_APPROVAL", "WAITING_PLAN_APPROVAL", "WAITING_TEST_APPROVAL", "WAITING_PR_APPROVAL")


def project_view(project: Project) -> dict:
    return {"id": str(project.id), "key": project.key, "repo_url": project.repo_url,
            "default_branch": project.default_branch, "readiness": project.readiness,
            "report": project.readiness_report.get("items", []), "checked_commit": project.checked_commit,
            "updated_at": project.updated_at.isoformat() if project.updated_at else None}


def waiting(run: Run) -> dict | None:
    if run.awaiting_start:
        return {"role": STAGE_STARTER[run.awaiting_start].value, "action": "start", "stage": run.awaiting_start}
    role = WAITING_FOR.get(run.status)
    if run.kind == "sds" and run.status == "WAITING_SPEC_APPROVAL":
        role = HumanRole.TECH_LEAD  # the architecture is the Tech Lead's call
    if role:
        return {"role": role.value, "action": run.status.lower()}
    if needs_classification(run):
        return {"role": "tech_lead", "action": "classify"}
    return None


def run_summary(run: Run, project_key: str) -> dict:
    return {"id": str(run.id), "title": run.title, "kind": run.kind, "project": project_key, "status": run.status,
            "version": run.version, "reason": run.status_reason, "waiting": waiting(run),
            "experiment": run.experiment, "updated_at": run.updated_at.isoformat(),
            "created_at": run.created_at.isoformat()}


def _document(document: Document) -> dict:
    return {"id": str(document.id), "kind": document.kind, "version": document.version, "sha256": document.sha256,
            "author_kind": document.author_kind, "author": document.author, "content": document.content,
            "repo_path": document.repo_path, "commit_sha": document.commit_sha,
            "findings": (document.data or {}).get("sync_findings", []),
            # the per-source chunks are kept for the next round only; the page needs the merged inventory
            **({"inventory": {k: v for k, v in (document.data or {}).items() if k != "chunks"}}
               if document.kind == "inventory" else {}),
            "created_at": document.created_at.isoformat()}


def _plan(plan: Plan) -> dict:
    return {"id": str(plan.id), "version": plan.version, "sha256": plan.sha256, "author_kind": plan.author_kind,
            "author": plan.author, "plan": plan.payload, "test_plan": plan.test_plan,
            "grounding": plan.grounding_report, "commit_sha": plan.commit_sha, "created_at": plan.created_at.isoformat()}


def _discovery(session: Session, run: Run) -> dict:
    """Questions by role, decisions and the project's discovery roles, for the Questions and Decisions tabs."""
    from controller.app.discovery import questions as q
    from controller.app.discovery.service import discovery_config
    run_questions, run_decisions = q.as_dicts(session, run.id)
    return {"roles": [r.model_dump() for r in discovery_config(session, run).roles], "questions": run_questions,
            "decisions": run_decisions}


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
    task_ids = {t.id: t.external_task_id for t in session.scalars(select(Task).where(Task.run_id == run.id))}
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
                           for c in session.scalars(select(Clarification).where(Clarification.run_id == run.id,
                                                                                Clarification.status.is_(None))
                                                    .order_by(Clarification.created_at))],
        **(_discovery(session, run) if run.kind == "discovery" else {}),
        "checklist": evaluate_gate(session, run) if run.status in WAITING_GATES else None,
        "sources": [{"id": str(s.id), "filename": s.filename, "kind": s.kind, "sha256": s.sha256, "size": s.size,
                     "pages": s.pages, "has_text": bool(s.text_path) or s.kind == "text", "uploaded_by": s.uploaded_by,
                     "purpose": s.purpose,
                     "created_at": s.created_at.isoformat()}
                    for s in session.scalars(select(Source).where(Source.run_id == run.id, Source.removed.is_(False))
                                             .order_by(Source.created_at))],
        "approvals": [{"stage": a.stage, "decision": a.decision, "actor": a.actor, "role": a.acting_role,
                       "reason": a.reason, "checklist": a.checklist, "created_at": a.created_at.isoformat()}
                      for a in session.scalars(select(Approval).where(Approval.run_id == run.id)
                                               .order_by(Approval.created_at))],
        "pull_request": None if pull is None else {
            "url": pull.url, "number": pull.number, "head_sha": pull.head_sha, "ci_status": pull.ci_status,
            "review_status": pull.review_status, "merged_sha": pull.merged_sha, "simulated": pull.simulated},
        "executions": [{"id": str(e.id), "kind": e.kind, "role": e.role, "status": e.status, "attempts": e.attempts,
                        "engine": e.engine, "error": e.error, "task": task_ids.get(e.task_id),
                        "started_at": e.started_at.isoformat() if e.started_at else None,
                        "finished_at": e.finished_at.isoformat() if e.finished_at else None} for e in executions],
        "task_steps": [{"task": task_ids.get(e.task_id), "steps": _steps(e.result)} for e in executions
                       if e.kind == "code_task" and e.result],
    })
    return detail


def _steps(result: dict) -> list[dict]:
    """What each doing role did in one task execution, without logs or prompts."""
    out = []
    for step in result.get("steps", []):
        evidence = step.get("evidence") or {}
        out.append({"state": step["state"], "role": step["role"], "ok": step["ok"], "detail": step["detail"][:2000],
                    "dispute": bool(step.get("dispute")),
                    "executor": evidence.get("executor"), "commit": evidence.get("commit"),
                    "seconds": evidence.get("seconds") or sum(r.get("seconds") or 0 for r in evidence.get("reports", [])),
                    "files": step.get("changed_files", [])})
    return out


def events_view(session: Session, run_id) -> list[dict]:
    return [{"id": e.id, "type": e.event_type, "actor": e.actor, "role": e.acting_role, "payload": e.payload,
             "created_at": e.created_at.isoformat()}
            for e in session.scalars(select(Event).where(Event.run_id == run_id).order_by(Event.id))]


def inbox(session: Session) -> list[dict]:
    items = []
    rows = session.execute(select(Run, Project.key).join(Project, Project.id == Run.project_id)
                           .order_by(Run.updated_at)).all()
    for run, key in rows:
        if run.experiment or (run.status not in ACTIVE and not needs_classification(run)):
            continue  # experiment runs are followed on the Evals page
        need = waiting(run)
        if run.kind == "discovery" and run.status == "CLARIFYING":
            # Questions for you: one card per role that has open questions, besides the PO's card for the round.
            from controller.app.discovery import questions as q
            from controller.app.discovery.service import discovery_config
            names = {r.id: r.name for r in discovery_config(session, run).roles}
            for owner, count in sorted(q.open_count(session, run.id).items()):
                items.append({**run_summary(run, key), "role": owner, "role_name": names.get(owner, owner),
                              "action": "answer_questions", "stage": None, "questions": count})
        if need:
            items.append({**run_summary(run, key), **{"role": need["role"], "action": need["action"],
                                                       "stage": need.get("stage")}})
    return items


def stories_view(session: Session, project: Project) -> dict:
    srs = mirror_for(project).read_file(project.checked_commit, SRS_PATH) if project.checked_commit else None
    latest: dict[str, dict] = {}
    rows = session.execute(select(Run, Document.data).join(Document, Document.run_id == Run.id)
                           .where(Run.project_id == project.id, Run.kind == "feature", Document.kind == "intent",
                                  Document.version == 1).order_by(Run.created_at)).all()
    for run, data in rows:
        story = (data or {}).get("story")
        if story:
            latest[story] = {"id": str(run.id), "status": run.status}
    return {"commit": project.checked_commit, "has_srs": srs is not None,
            "features": [{**f.model_dump(), "stories": [{**s.model_dump(), "run": latest.get(s.id)} for s in f.stories]}
                         for f in srs_features(srs or "")]}
