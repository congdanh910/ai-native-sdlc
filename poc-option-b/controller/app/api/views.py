"""JSON shapes returned to the web control room."""

import json
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from controller.app.models import (Approval, Artifact, Clarification, Document, Event, Execution, Plan, Project, PullRequest,
                                   Run, Source, Task)
from controller.app.project_docs import SRS_PATH, srs_features
from controller.app.projects.registry import mirror_for
from controller.app.coordinator import artifact, evaluate_gate
from controller.app.services import needs_classification
from controller.app.states import ACTIVE, STAGE_STARTER, WAITING_FOR, HumanRole, RunStatus


WAITING_GATES = ("WAITING_BASELINE_APPROVAL", "WAITING_ONBOARDING_APPROVAL", "WAITING_ACCEPTANCE_APPROVAL", "WAITING_OPTIONS_APPROVAL", "WAITING_UI_APPROVAL", "WAITING_SPEC_APPROVAL", "WAITING_PLAN_APPROVAL", "WAITING_TEST_APPROVAL", "WAITING_PR_APPROVAL")


def project_view(project: Project) -> dict:
    return {"id": str(project.id), "key": project.key, "repo_url": project.repo_url,
            "default_branch": project.default_branch, "readiness": project.readiness,
            "report": project.readiness_report.get("items", []), "checked_commit": project.checked_commit,
            "updated_at": project.updated_at.isoformat() if project.updated_at else None}


def ai_deciding(session: Session, run: Run) -> bool:
    """A disputed task whose brief is being written while disputes are settled by the team: nobody needs to act."""
    if run.status != RunStatus.NEEDS_HUMAN:
        return False
    if not session.scalar(select(Execution.id).where(Execution.run_id == run.id, Execution.kind == "advise",
                                                     Execution.status.in_(("queued", "running"))).limit(1)):
        return False
    if not session.scalar(select(Task.id).where(Task.run_id == run.id, Task.status == "DISPUTED").limit(1)):
        return False
    from controller.app.coordinator import artifact
    from controller.app.projects.config import EffectivePolicy
    raw = artifact(session, run.id, "effective_policy")
    return bool(raw) and EffectivePolicy.model_validate_json(raw).disputes.auto


def waiting(run: Run, session: Session | None = None) -> dict | None:
    if session is not None and ai_deciding(session, run):
        return {"role": "code_reviewer", "action": "ai_deciding"}
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


def run_summary(run: Run, project_key: str, session: Session | None = None) -> dict:
    return {"id": str(run.id), "title": run.title, "kind": run.kind, "project": project_key, "status": run.status,
            "version": run.version, "reason": run.status_reason, "waiting": waiting(run, session),
            "experiment": run.experiment, "updated_at": run.updated_at.isoformat(),
            "created_at": run.created_at.isoformat()}


def _document(document: Document) -> dict:
    return {"id": str(document.id), "kind": document.kind, "version": document.version, "sha256": document.sha256,
            "author_kind": document.author_kind, "author": document.author, "content": document.content,
            "repo_path": document.repo_path, "commit_sha": document.commit_sha,
            "findings": (document.data or {}).get("sync_findings", []),
            "revision": (document.data or {}).get("revision"),
            **({"choices": _option_choices(document.data)} if document.kind == "options" and document.data else {}),
            **({"acceptance": {k: (document.data or {}).get(k) for k in ("cases", "criteria", "attempt", "url", "problems", "proven")}}
               if document.kind == "acceptance" else {}),
            **({"ui": _ui_concepts(document.data)} if document.kind == "ui_concepts" and document.data else {}),
            # the per-source chunks are kept for the next round only; the page needs the merged inventory
            **({"inventory": {k: v for k, v in (document.data or {}).items() if k != "chunks"}}
               if document.kind == "inventory" else {}),
            "created_at": document.created_at.isoformat()}


def _ui_concepts(data: dict) -> dict:
    """What the Product Owner picks from: each concept with its palette, contrast checks and prototype screens."""
    from controller.app.ui import slug
    brief = data.get("brief") or {}
    return {"notes": data.get("notes", ""), "devices": brief.get("devices", ["desktop"]),
            "accessibility": brief.get("accessibility", "wcag-aa"), "brand": brief.get("brand", {}),
            "concepts": [{"id": c["id"], "name": c["name"], "preset": c.get("preset", ""), "layout": c["layout"],
                          "density": c.get("density", ""), "rationale": c["rationale"], "colors": c["tokens"]["colors"],
                          "font": c["tokens"]["font_family"], "checks": (data.get("checks") or {}).get(c["id"], []),
                          "screens": [{"name": s["name"], "role": s.get("role", ""), "slug": slug(s["name"])}
                                      for s in c["screens"]]}
                         for c in data.get("concepts", [])]}


def _option_choices(data: dict) -> dict:
    """What the Solution Architect picks from: the options and the hosting variants of each, with the recommendations."""
    from controller.app.project_docs import number_options, platform_name, stored_options
    options = number_options(stored_options(data))
    return {"recommendation": options.recommendation, "hosting_basis": data.get("hosting_basis", "stated"),
            "options": [{"id": o.id, "name": o.name, "recommended_hosting": o.recommended_hosting,
                         "hosting": [{"id": v.id, "name": v.name, "platform": platform_name(v.platform)} for v in o.hosting]}
                        for o in options.options]}


def _plan(plan: Plan) -> dict:
    return {"id": str(plan.id), "version": plan.version, "sha256": plan.sha256, "author_kind": plan.author_kind,
            "author": plan.author, "plan": plan.payload, "test_plan": plan.test_plan,
            "grounding": plan.grounding_report, "commit_sha": plan.commit_sha, "created_at": plan.created_at.isoformat()}


def _discovery(session: Session, run: Run) -> dict:
    """Questions by role, decisions and the project's discovery roles, for the Questions and Decisions tabs."""
    from controller.app.discovery import questions as q
    from controller.app.discovery.service import counted_rounds, discovery_config
    run_questions, run_decisions = q.as_dicts(session, run.id)
    from controller.app.documents import latest_document
    config = discovery_config(session, run)
    latest = latest_document(session, run.id, "inventory")
    return {"roles": [r.model_dump() for r in config.roles], "questions": run_questions, "decisions": run_decisions,
            "rounds": {"used": counted_rounds(session, run.id), "limit": config.limits.max_rounds}}


def _preview_view(session: Session, run: Run) -> dict | None:
    """The run's preview (status, url, demo accounts), when the project has one; None otherwise."""
    from controller.app.coordinator import artifact, preview_state
    from controller.app.projects.config import EffectivePolicy
    raw = artifact(session, run.id, "effective_policy")
    policy = EffectivePolicy.model_validate_json(raw) if raw else None
    if policy is None or policy.preview is None:
        return None
    from pathlib import Path
    from controller.app.doing.changes import head, head_time
    from controller.app.preview import missing_parts
    state = preview_state(session, run.id) or {"status": "stopped", "detail": "not started"}
    missing = missing_parts(Path(run.worktree_path), policy.preview) if run.worktree_path else ["the run's branch"]
    if missing and state.get("status") not in ("running", "starting", "stopping"):
        # an earlier try failed for the same reason: show why it cannot start now, not the old error
        state = {"status": "unavailable", "detail": f"Available once the code exists: {', '.join(missing)} not in the "
                                                     "run's branch yet (the run's coding creates them)"}
    elif state.get("status") == "failed" and run.worktree_path:
        # a failed start on an earlier commit: the code changed since, so its error may no longer hold. States before
        # 2026-10-22 name no commit and are compared by time (the database's clock may run apart from git's)
        failed_at = session.scalar(select(Artifact.created_at).where(Artifact.run_id == run.id, Artifact.kind == "preview")
                                   .order_by(Artifact.created_at.desc()).limit(1))
        stale = (state["commit"] != head(Path(run.worktree_path))) if state.get("commit") else \
            bool(failed_at and head_time(Path(run.worktree_path)) > failed_at)
        if stale:
            state = {"status": "stopped", "detail": "The last start failed on earlier code; the branch changed since, "
                                                     f"start it again. Earlier error: {state.get('detail', '')[:300]}"}
    from controller.app.services import preview_locked
    return {**state, "accounts": [a.model_dump() for a in policy.preview.accounts], "available": bool(run.worktree_path),
            "missing": missing, "locked": preview_locked(run, start=state.get("status") not in ("running", "starting"))}


def _overlaps(session: Session, run: Run) -> list[dict]:
    """At the plan gate: open runs that change files this plan changes."""
    if run.status != RunStatus.WAITING_PLAN_APPROVAL:
        return []
    from controller.app.backlog import overlaps
    return overlaps(session, run)


def _waiting_for(session: Session, run: Run) -> list[dict]:
    if run.status != RunStatus.READY:
        return []
    from controller.app.backlog import waiting_on
    return [{"run": str(r.id), "title": r.title, "status": r.status} for r in waiting_on(session, run)]


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
    detail = run_summary(run, project.key, session)
    detail.update({"preview": _preview_view(session, run)})
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
        "already_built": _already_built(session, run, tasks, executions),
        "advice": _advice(session, run, executions),
        "defect_check": _defect_check(session, run),
        "overlaps": _overlaps(session, run),
        "waiting_for": _waiting_for(session, run),
        "main_sync": json.loads(artifact(session, run.id, "main_sync") or "null"),
        "auto_decisions": [{**(e.payload or {}), "at": e.created_at.isoformat()} for e in session.scalars(
            select(Event).where(Event.run_id == run.id, Event.event_type == "dispute.auto_ruled").order_by(Event.id))],
    })
    return detail


def _defect_check(session: Session, run: Run) -> dict | None:
    """The SDS check of a person's defect notes, while the run waits on a conflict it found."""
    from controller.app.coordinator import DEFECTS_CONFLICT, artifact
    if run.status != "NEEDS_HUMAN" or DEFECTS_CONFLICT not in (run.status_reason or ""):
        return None
    raw = artifact(session, run.id, "defect_check")
    return json.loads(raw) if raw else None


def _advice(session: Session, run: Run, executions: list[Execution]) -> dict | None:
    """The Code Reviewer's brief on the stop the run waits on, or that one is being prepared; None otherwise."""
    if run.status != "NEEDS_HUMAN":
        return None
    pending = any(e.kind == "advise" and e.status in ("queued", "running") for e in executions)
    for content in session.scalars(select(Artifact.content).where(Artifact.run_id == run.id, Artifact.kind == "advice")
                                   .order_by(Artifact.created_at.desc())):
        stored = json.loads(content)
        if stored.get("for") == (run.status_reason or ""):
            skipped = next((json.loads(c) for c in session.scalars(
                select(Artifact.content).where(Artifact.run_id == run.id, Artifact.kind == "auto_skipped")
                .order_by(Artifact.created_at.desc())) if json.loads(c).get("for") == stored["for"]), None)
            return {"pending": pending, "advice": stored["advice"], "engine": stored.get("engine"),
                    "not_decided": skipped["why"] if skipped else None}
    failed = next((e.error for e in reversed(executions) if e.kind == "advise" and e.status == "failed"), None)
    return {"pending": pending, "advice": None, "error": None if pending else failed}


def _already_built(session: Session, run: Run, tasks: list[Task], executions: list[Execution]) -> dict | None:
    """After a red check found a task's new tests passing before any code: what the Tech Lead needs to tell code
    already built from tests that check too little. The QA Engineer's last note, the tests it added, the commands
    that passed, and the earlier commits of this run that changed the task's files."""
    from controller.app.coordinator import artifact
    from controller.app.doing import changes
    from controller.app.projects.repo import GitError
    from controller.app.services import ALREADY_PASS
    if run.status != "NEEDS_HUMAN" or ALREADY_PASS not in (run.status_reason or ""):
        return None
    task = next((t for t in tasks if t.status == "FAILED"), None)
    execution = next((e for e in reversed(executions)
                      if task and e.task_id == task.id and e.kind == "code_task" and e.result), None)
    if task is None or execution is None:
        return None
    steps = execution.result.get("steps", [])
    written = [s for s in steps if s["state"] == "WRITING_TESTS" and s["ok"]]
    red = [s for s in steps if s["state"] == "RED_CHECK"]
    note = (written[-1].get("evidence") or {}).get("message", "") if written else ""
    commit = (written[-1].get("evidence") or {}).get("commit") if written else None
    files = task.payload.get("modify", []) + task.payload.get("create", [])
    worktree = Path(run.worktree_path) if run.worktree_path else None
    tests, earlier = [], []
    if worktree and worktree.is_dir():
        try:
            tests = [{"file": f, "name": n} for f, n in changes.test_names(worktree, commit)] if commit else []
            base = artifact(session, run.id, "task_base") or run.base_commit
            if run.base_commit and base:  # before this plan's coding: what the run's earlier plans built
                earlier = [c for c in changes.commits_touching(worktree, run.base_commit, base, files)
                           if c["subject"].startswith(("feat(", "build("))][:8]
        except GitError:
            pass
    return {"task": task.external_task_id, "title": task.payload["title"], "files": files, "qa_note": note[-1500:],
            "tests": tests[:30], "red_check": red[-1]["detail"][:600] if red else "", "earlier_commits": earlier}


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
        need = waiting(run, session)
        if need and need["action"] == "ai_deciding":
            continue  # the Code Reviewer settles the dispute first; a person is asked only if it cannot
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
