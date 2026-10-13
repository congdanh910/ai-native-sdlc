"""Delivery Coordinator: runs queued work through team roles and moves runs forward.

Each job is handled in three steps so that no database transaction stays open
while a role works:

1. load: read everything the role may see from stored artifacts (short transaction)
2. compute: call the team role (no database access)
3. apply: re-check the run, store the artifact, transition and queue the next job
"""

from dataclasses import dataclass, field
import json
from pathlib import Path
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from controller.app.config import DATA_DIR
from controller.app.documents import (add_document, add_plan, latest_document, latest_plan, sha256_text,
                                      thread_path)
from controller.app.events import record_event
from controller.app.jobs import COORDINATOR, enqueue, finish
from controller.app.models import Artifact, Clarification, Execution, Project, PullRequest, Run, Task
from controller.app.projects.config import EffectivePolicy, effective_policy, matches_any
from controller.app.projects.registry import mirror_for, read_snapshot
from controller.app.projects.repo import Worktree
from controller.app.schemas import PlanPayload, PlanTask, Spec, TestPlan, render_plan, render_spec
from controller.app.state_machine import transition_run, transition_task
from controller.app.states import RunStatus, TaskStatus, TeamRole
from controller.app.team.base import RunContext, StepResult, Team

MAX_REMEDIATIONS = 2

# kind -> (role, has side effects, run status the job belongs to)
KINDS: dict[str, tuple[TeamRole, bool, RunStatus]] = {
    "preflight": (TeamRole.COORDINATOR, False, RunStatus.PREFLIGHT),
    "specify": (TeamRole.BUSINESS_ANALYST, False, RunStatus.SPECIFYING),
    "plan": (TeamRole.TECH_LEAD, False, RunStatus.PLANNING),
    "ground": (TeamRole.COORDINATOR, False, RunStatus.GROUNDING),
    "code_task": (TeamRole.DEVELOPER, True, RunStatus.CODING),
    "review": (TeamRole.CODE_REVIEWER, False, RunStatus.REVIEWING),
    "release": (TeamRole.RELEASE_ENGINEER, True, RunStatus.PR_OPEN),
}


class JobAborted(Exception):
    """The job cannot do its work; the run needs a human."""


def queue(session: Session, run: Run, kind: str, *, key: str | None = None, task: Task | None = None) -> Execution:
    role, side_effects, _ = KINDS[kind]
    return enqueue(session, run, kind, role.value, side_effects=side_effects, key=key or f"v{run.version}",
                   task=task)


# ---------------------------------------------------------------- stages

def stage_mode(run: Run, stage: str) -> str:
    return run.stage_modes.get(stage, "gated" if stage in ("spec", "plan") else "auto")


def enter_stage(session: Session, run: Run, stage: str, *, actor: str = COORDINATOR,
                acting_role: str | None = None) -> None:
    """Enter a stage. In manual mode the run waits there until the operator starts it."""
    target = {"spec": RunStatus.SPECIFYING, "plan": RunStatus.PLANNING,
              "coding": RunStatus.CODING, "review": RunStatus.REVIEWING}[stage]
    manual = stage_mode(run, stage) == "manual"
    transition_run(session, run, target, actor=actor, acting_role=acting_role,
                   awaiting_start=stage if manual else None,
                   reason=f"waiting for the operator to start {stage}" if manual else None)
    if not manual:
        schedule_stage(session, run, stage)


def schedule_stage(session: Session, run: Run, stage: str) -> None:
    if stage == "spec":
        queue(session, run, "specify")
    elif stage == "plan":
        queue(session, run, "plan")
    elif stage == "coding":
        schedule_tasks(session, run)
    elif stage == "review":
        queue(session, run, "review")


def current_tasks(session: Session, run: Run) -> list[Task]:
    plan = latest_plan(session, run.id)
    return list(session.scalars(select(Task).where(Task.run_id == run.id, Task.plan_id == plan.id)
                                .order_by(Task.external_task_id))) if plan else []


def schedule_tasks(session: Session, run: Run) -> None:
    """Queue every task whose dependencies passed; enter review when all passed."""
    tasks = current_tasks(session, run)
    passed = {task.external_task_id for task in tasks if task.status == TaskStatus.PASSED}
    if tasks and len(passed) == len(tasks):
        enter_stage(session, run, "review")
        return
    for task in tasks:
        if task.status == TaskStatus.PENDING and set(task.payload.get("depends_on", [])) <= passed:
            transition_task(session, task, TaskStatus.READY, actor=COORDINATOR, reason="dependencies passed")
        if task.status == TaskStatus.READY:
            queue(session, run, "code_task", key=f"{task.external_task_id}:a{task.attempt_count}", task=task)


def to_needs_human(session: Session, run: Run, reason: str, *, actor: str = COORDINATOR) -> None:
    transition_run(session, run, RunStatus.NEEDS_HUMAN, actor=actor, resume_status=run.status, reason=reason)


# ---------------------------------------------------------------- artifact thread

def worktree_for(project: Project, run: Run) -> Worktree:
    return Worktree(mirror_for(project), Path(run.worktree_path), run.branch)


def commit_thread(session: Session, run: Run, kind: str, content: str, message: str) -> str | None:
    if not run.commit_artifacts:
        return None
    project = session.get(Project, run.project_id)
    return worktree_for(project, run).commit_file(thread_path(run.id, kind), content, message)


def store_artifact(session: Session, run: Run, kind: str, role: TeamRole, content: str) -> None:
    session.add(Artifact(run_id=run.id, kind=kind, role=role.value, content=content, checksum=sha256_text(content)))


def artifact(session: Session, run_id: uuid.UUID, kind: str) -> str | None:
    return session.scalar(select(Artifact.content).where(Artifact.run_id == run_id, Artifact.kind == kind)
                          .order_by(Artifact.created_at.desc()).limit(1))


def on_claim(session: Session, execution: Execution) -> None:
    """Mark side-effecting work as started before it begins, so a crash leaves a visible trace."""
    if execution.kind == "code_task":
        task = session.get(Task, execution.task_id, with_for_update=True)
        if task.status == TaskStatus.READY:
            task.attempt_count += 1  # the next attempt gets a new idempotency key
            transition_task(session, task, TaskStatus.WRITING_TESTS, actor=TeamRole.QA_ENGINEER.value,
                            reason=f"execution {execution.id} started")


# ---------------------------------------------------------------- context

@dataclass
class Loaded:
    execution_id: uuid.UUID
    kind: str
    run_id: uuid.UUID
    run_version: int
    context: RunContext | None = None
    plan: PlanPayload | None = None
    tests: TestPlan | None = None
    task_payload: dict | None = None
    task_attempt: int = 0
    extra: dict = field(default_factory=dict)


def build_context(session: Session, run: Run) -> RunContext:
    policy = EffectivePolicy.model_validate_json(artifact(session, run.id, "effective_policy"))
    project = session.get(Project, run.project_id)
    files = mirror_for(project).list_files(run.base_commit)
    include = json.loads(artifact(session, run.id, "context_rules") or '{"include": ["**"], "exclude": []}')
    files = [f for f in files if matches_any(f, include["include"]) and not matches_any(f, include["exclude"])]
    spec = latest_document(session, run.id, "spec")
    answers = [(c.question, c.answer) for c in session.scalars(
        select(Clarification).where(Clarification.run_id == run.id, Clarification.answer.is_not(None)))]
    return RunContext(title=run.title, policy=policy, agents_md=artifact(session, run.id, "agents_md") or "",
                      review_md=artifact(session, run.id, "review_md"),
                      intent_md=latest_document(session, run.id, "intent").content, files=files,
                      spec_md=spec.content if spec else None, answers=answers)


def load(session: Session, execution: Execution) -> Loaded:
    run = session.get(Run, execution.run_id)
    loaded = Loaded(execution.id, execution.kind, run.id, run.version)
    if execution.kind in ("preflight", "ground", "release"):
        return loaded
    loaded.context = build_context(session, run)
    plan = latest_plan(session, run.id)
    if plan:
        loaded.plan = PlanPayload.model_validate(plan.payload)
        loaded.tests = TestPlan.model_validate(plan.test_plan)
    if execution.task_id:
        task = session.get(Task, execution.task_id)
        loaded.task_payload, loaded.task_attempt = task.payload, task.attempt_count
    return loaded


# ---------------------------------------------------------------- compute (no database)

def compute(team: Team, loaded: Loaded) -> dict:
    context = loaded.context
    if loaded.kind == "specify":
        return {"spec": team.specify(context).model_dump()}
    if loaded.kind == "plan":
        plan = team.plan(context)
        tests = team.design_tests(context, plan)
        return {"plan": plan.model_dump(), "tests": tests.model_dump()}
    if loaded.kind == "code_task":
        return {"steps": _work_task(team, loaded)}
    if loaded.kind == "review":
        return {"review": team.review(context, loaded.plan, loaded.tests).model_dump()}
    return {}


def _work_task(team: Team, loaded: Loaded) -> list[dict]:
    """QA Engineer writes tests, Validation Gate red-checks, Developer implements, Validation Gate validates."""
    task = PlanTask.model_validate(loaded.task_payload)
    context = loaded.context

    def step(state: TaskStatus, role: TeamRole, result: StepResult) -> dict:
        return {"state": state.value, "role": role.value, "ok": result.ok, "detail": result.detail,
                "dispute": result.dispute, "changed_files": result.changed_files}

    steps = [step(TaskStatus.WRITING_TESTS, TeamRole.QA_ENGINEER, team.write_tests(context, task, loaded.tests))]
    if not steps[-1]["ok"]:
        return steps
    steps.append(step(TaskStatus.RED_CHECK, TeamRole.VALIDATION_GATE, team.red_check(context, task)))
    if not steps[-1]["ok"]:
        return steps
    steps.append(step(TaskStatus.CODING, TeamRole.DEVELOPER, team.implement(context, task, attempt=1)))
    for remediation in range(MAX_REMEDIATIONS + 1):
        if not steps[-1]["ok"] or steps[-1]["dispute"]:
            return steps
        steps.append(step(TaskStatus.VALIDATING, TeamRole.VALIDATION_GATE, team.validate(context, task)))
        if steps[-1]["ok"] or remediation == MAX_REMEDIATIONS:
            return steps
        steps.append(step(TaskStatus.REMEDIATING, TeamRole.DEVELOPER,
                          team.implement(context, task, attempt=remediation + 2)))
    return steps


# ---------------------------------------------------------------- apply

def apply(session: Session, loaded: Loaded, result: dict, team: Team) -> None:
    execution = session.get(Execution, loaded.execution_id, with_for_update=True)
    run = session.get(Run, loaded.run_id, with_for_update=True)
    expected = KINDS[loaded.kind][2]
    if run.status != expected or (loaded.kind != "code_task" and run.version != loaded.run_version):
        finish(session, execution, "discarded", error=f"run moved to {run.status} v{run.version} meanwhile")
        record_event(session, "job.discarded", actor=COORDINATOR, run_id=run.id,
                     payload={"kind": loaded.kind, "execution": str(execution.id)})
        return
    execution.engine = team.engine
    handler = {"preflight": _apply_preflight, "specify": _apply_specify, "plan": _apply_plan,
               "ground": _apply_ground, "code_task": _apply_code_task, "review": _apply_review,
               "release": _apply_release}[loaded.kind]
    handler(session, run, execution, result, team)
    if execution.status == "running":
        finish(session, execution, "succeeded", result=result)


def _apply_preflight(session: Session, run: Run, execution: Execution, result: dict, team: Team) -> None:
    project = session.get(Project, run.project_id)
    mirror = mirror_for(project)
    mirror.fetch()
    base = run.base_commit or mirror.resolve(project.default_branch)
    snapshot, items = read_snapshot(mirror, base)
    if snapshot is None:
        failed = "; ".join(f"{i.name}: {i.detail}" for i in items if i.status == "fail")
        finish(session, execution, "failed", error=failed)
        to_needs_human(session, run, f"onboarding files are not usable at {base[:12]}: {failed}")
        return
    policy = effective_policy(snapshot.config)
    store_artifact(session, run, "project_config", TeamRole.COORDINATOR,
                   mirror.read_file(base, ".sdlc/project.yaml"))
    store_artifact(session, run, "effective_policy", TeamRole.COORDINATOR, policy.model_dump_json())
    store_artifact(session, run, "context_rules", TeamRole.COORDINATOR,
                   json.dumps(snapshot.config.context.model_dump()))
    store_artifact(session, run, "agents_md", TeamRole.COORDINATOR, snapshot.agents_md)
    if snapshot.review_md:
        store_artifact(session, run, "review_md", TeamRole.COORDINATOR, snapshot.review_md)
    run.base_commit = base
    run.stage_modes = policy.stages.model_dump()
    run.commit_artifacts = policy.commit_artifacts
    run.branch = f"sdlc/run-{str(run.id)[:8]}"
    run.worktree_path = str(DATA_DIR / "worktrees" / str(run.id))
    if run.commit_artifacts:
        worktree_for(project, run).create(base)
        intent = latest_document(session, run.id, "intent")
        intent.commit_sha = commit_thread(session, run, "intent", intent.content,
                                          f"sdlc: intent for {run.title}")
    enter_stage(session, run, "spec")


def _apply_specify(session: Session, run: Run, execution: Execution, result: dict, team: Team) -> None:
    spec = Spec.model_validate(result["spec"])
    blocking = [q for q in spec.questions if q.blocking]
    if blocking:
        for question in blocking:
            session.add(Clarification(run_id=run.id, question=question.question, blocking=True))
        transition_run(session, run, RunStatus.NEEDS_CLARIFICATION, actor=TeamRole.BUSINESS_ANALYST.value,
                       reason=f"{len(blocking)} blocking question(s) for the Product Owner")
        return
    document = add_document(session, run.id, "spec", render_spec(run.title, spec),
                            author_kind="ai", author=TeamRole.BUSINESS_ANALYST.value, data=result["spec"])
    if stage_mode(run, "spec") == "auto":
        accept_spec(session, run, document, actor=COORDINATOR)
    else:
        transition_run(session, run, RunStatus.WAITING_SPEC_APPROVAL, actor=TeamRole.BUSINESS_ANALYST.value,
                       reason=f"spec v{document.version} ready")


def accept_spec(session: Session, run: Run, document, *, actor: str, acting_role: str | None = None) -> None:
    document.commit_sha = commit_thread(session, run, "spec", document.content,
                                        f"sdlc: spec v{document.version} accepted")
    enter_stage(session, run, "plan", actor=actor, acting_role=acting_role)


def _apply_plan(session: Session, run: Run, execution: Execution, result: dict, team: Team) -> None:
    plan = add_plan(session, run.id, result["plan"], result["tests"], author_kind="ai",
                    author=TeamRole.TECH_LEAD.value)
    record_event(session, "plan.proposed", actor=TeamRole.TECH_LEAD.value, run_id=run.id,
                 payload={"plan": str(plan.id), "version": plan.version,
                          "test_cases": len(result["tests"]["cases"]), "qa_analyst": team.engine})
    transition_run(session, run, RunStatus.GROUNDING, actor=TeamRole.TECH_LEAD.value)
    queue(session, run, "ground")


def grounding_report(plan_payload: dict, test_plan: dict) -> dict:
    """Placeholder until the deterministic grounding check of B3."""
    return {"status": "skipped", "findings": ["The grounding check arrives in B3; review file references by hand."]}


def _apply_ground(session: Session, run: Run, execution: Execution, result: dict, team: Team) -> None:
    plan = latest_plan(session, run.id)
    plan.grounding_report = grounding_report(plan.payload, plan.test_plan)
    transition_run(session, run, RunStatus.WAITING_PLAN_APPROVAL, actor=COORDINATOR,
                   reason=f"plan v{plan.version} ready; grounding {plan.grounding_report['status']}")


def plan_markdown(run: Run, plan) -> str:
    return render_plan(run.title, plan.version, PlanPayload.model_validate(plan.payload),
                       TestPlan.model_validate(plan.test_plan), plan.grounding_report)


def _apply_code_task(session: Session, run: Run, execution: Execution, result: dict, team: Team) -> None:
    task = session.get(Task, execution.task_id, with_for_update=True)
    if task.status != TaskStatus.WRITING_TESTS:
        finish(session, execution, "discarded", error=f"task is {task.status}")
        return
    for step in result["steps"]:
        state = TaskStatus(step["state"])
        if state != task.status:
            transition_task(session, task, state, actor=step["role"], reason=step["detail"])
        else:
            record_event(session, "task.step", actor=step["role"], run_id=run.id,
                         payload={"task": task.external_task_id, "state": state.value, "detail": step["detail"]})
        if step["dispute"]:
            transition_task(session, task, TaskStatus.DISPUTED, actor=step["role"], reason=step["detail"])
            to_needs_human(session, run, f"{task.external_task_id}: the Developer disputes a test: {step['detail']}")
            return
        if not step["ok"] and state in (TaskStatus.WRITING_TESTS, TaskStatus.RED_CHECK, TaskStatus.CODING,
                                        TaskStatus.REMEDIATING):
            transition_task(session, task, TaskStatus.FAILED, actor=step["role"], reason=step["detail"])
            to_needs_human(session, run, f"{task.external_task_id} failed at {state.value}: {step['detail']}")
            return
    last = result["steps"][-1]
    if last["state"] == TaskStatus.VALIDATING and last["ok"]:
        transition_task(session, task, TaskStatus.PASSED, actor=TeamRole.VALIDATION_GATE.value, reason="validation passed")
        schedule_tasks(session, run)
    else:
        transition_task(session, task, TaskStatus.FAILED, actor=TeamRole.VALIDATION_GATE.value,
                        reason="validation still failing after remediation")
        to_needs_human(session, run, f"{task.external_task_id}: validation failed after {MAX_REMEDIATIONS} remediations")


def _apply_review(session: Session, run: Run, execution: Execution, result: dict, team: Team) -> None:
    review = result["review"]
    document = add_document(session, run.id, "review", review["markdown"], author_kind="ai",
                            author=TeamRole.CODE_REVIEWER.value, data=review)
    document.commit_sha = commit_thread(session, run, "review", document.content, "sdlc: review")
    transition_run(session, run, RunStatus.PR_OPEN, actor=TeamRole.CODE_REVIEWER.value,
                   reason=f"review verdict: {review['verdict']}")
    queue(session, run, "release")


def execution_report(session: Session, run: Run) -> str:
    project = session.get(Project, run.project_id)
    lines = [f"# Execution report: {run.title}", "", f"- Project: {project.key}",
             f"- Base commit: `{run.base_commit}`", f"- Branch: `{run.branch}`", "", "## Artifact thread", "",
             "| Artifact | Version | SHA-256 | Author |", "|---|---|---|---|"]
    for kind in ("intent", "spec", "review"):
        document = latest_document(session, run.id, kind)
        if document:
            lines.append(f"| {kind}.md | v{document.version} | `{document.sha256[:16]}` | {document.author} |")
    plan = latest_plan(session, run.id)
    if plan:
        lines.append(f"| plan.md | v{plan.version} | `{plan.sha256[:16]}` | {plan.author} |")
    lines += ["", "## Tasks", "", "| Task | Status | Attempts |", "|---|---|---|"]
    for task in current_tasks(session, run):
        lines.append(f"| {task.external_task_id} {task.payload['title']} | {task.status} | {task.attempt_count} |")
    engines = sorted({e for e in session.scalars(select(Execution.engine).where(Execution.run_id == run.id)) if e})
    lines += ["", f"Team engine(s): {', '.join(engines) or 'none'}", ""]
    return "\n".join(lines)


def _apply_release(session: Session, run: Run, execution: Execution, result: dict, team: Team) -> None:
    report = add_document(session, run.id, "report", execution_report(session, run), author_kind="ai",
                          author=TeamRole.RELEASE_ENGINEER.value)
    head = commit_thread(session, run, "report", report.content, "sdlc: execution report")
    report.commit_sha = head
    release = team.open_pull_request(build_context(session, run), run.branch)
    pull = session.scalar(select(PullRequest).where(PullRequest.run_id == run.id))
    if pull is None:  # after requested changes the same pull request gets a new head
        pull = PullRequest(run_id=run.id)
        session.add(pull)
    pull.number, pull.url, pull.head_sha, pull.simulated = release.number, release.url, head, release.simulated
    pull.ci_status, pull.review_status = ("simulated" if release.simulated else "pending"), "pending"
    transition_run(session, run, RunStatus.WAITING_PR_APPROVAL, actor=TeamRole.RELEASE_ENGINEER.value,
                   reason="simulated pull request (B2): the branch exists only in the local mirror"
                   if release.simulated else f"draft PR {release.url}")
