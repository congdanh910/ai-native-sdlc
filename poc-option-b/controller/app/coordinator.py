"""Delivery Coordinator: runs queued work through team roles and moves runs forward.

Each job is handled in three steps so that no database transaction stays open
while a role works:

1. load: read everything the role may see from stored artifacts (short transaction)
2. compute: call the team role (no database access)
3. apply: re-check the run, store the artifact, transition and queue the next job
"""

from dataclasses import dataclass, field
import dataclasses
import json
import logging
import re
from pathlib import Path
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from controller.app.config import DATA_DIR, get_settings
from controller.app.context.builder import build_context as build_code_context
from controller.app.context.grounding import check_with_bundle
from controller.app.doing import changes, github
from controller.app.doing.github import repo_slug
from controller.app.documents import (THREAD_DIR, add_document, add_plan, latest_document, latest_plan,
                                      section_changes, sha256_text, thread_path)
from controller.app.events import record_event
from controller.app.jobs import COORDINATOR, enqueue, finish
from controller.app.models import Approval, Artifact, Document, Clarification, Execution, Project, PullRequest, Run, Task
from controller.app.feature_docs import (PHASE_HEADING, FeatureRef, link_once, phase_number, plan_header,
                                         plan_section, requirements_header, requirements_section, resolve_feature,
                                         sdlc_yaml, sds_feature_heading, spec_header, spec_section, srs_feature_heading,
                                         tasks_header, tasks_section, tc_numbers, test_cases_header, test_cases_section,
                                         upsert_section)
from controller.app import analysis, checklists, joblog
from controller.app.project_docs import (SDS_PATH, SRS_PATH, ArchitectureBrief, Options, Sds, Srs, check_chosen_option,
                                         check_options, check_sds_depth, check_sds_markdown, check_srs_markdown,
                                         check_srs_trace, number_options, render_options, render_sds, render_srs,
                                         sds_sync_findings, srs_features, stored_options)
from controller.app.projects.config import EffectivePolicy, effective_policy, matches_any
from controller.app.projects.registry import mirror_for, read_snapshot
from controller.app.projects.repo import Worktree
from controller.app.schemas import (PlanPayload, PlanTask, Review, Spec, TestPlan, parse_criteria, render_plan,
                                    render_review, render_spec)
from controller.app.state_machine import transition_run, transition_task
from controller.app.states import RunKind, RunStatus, TaskStatus, TeamRole
from controller.app.team.base import ReleaseResult, RunContext, StepResult, Team
from controller.app.team.tidy import tidy_plan, tidy_spec, tidy_tests

log = logging.getLogger("sdlc.coordinator")

# kind -> (role, has side effects, run status the job belongs to)
KINDS: dict[str, tuple[TeamRole, bool, RunStatus]] = {
    "preflight": (TeamRole.COORDINATOR, False, RunStatus.PREFLIGHT),
    "specify": (TeamRole.BUSINESS_ANALYST, False, RunStatus.SPECIFYING),
    "plan": (TeamRole.TECH_LEAD, False, RunStatus.PLANNING),
    "ground": (TeamRole.COORDINATOR, False, RunStatus.GROUNDING),
    "design_tests": (TeamRole.QA_ANALYST, False, RunStatus.DESIGNING_TESTS),
    "code_task": (TeamRole.DEVELOPER, True, RunStatus.CODING),
    "review": (TeamRole.CODE_REVIEWER, False, RunStatus.REVIEWING),
    "release": (TeamRole.RELEASE_ENGINEER, True, RunStatus.PR_OPEN),
    "extract": (TeamRole.BUSINESS_ANALYST, False, RunStatus.EXTRACTING),
    "options": (TeamRole.ARCHITECT, False, RunStatus.DESIGNING_OPTIONS),
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


def feature_ref(session: Session, run: Run) -> FeatureRef | None:
    """The SRS story a feature run delivers and its specs/ folder, fixed at preflight; None without an SRS."""
    raw = artifact(session, run.id, "feature")
    return FeatureRef.model_validate_json(raw) if raw else None


def _spec_model(session: Session, run: Run) -> Spec:
    """The Business Analyst's last structured spec: a human edit keeps only Markdown, its notes stay the AI's."""
    documents = session.scalars(select(Document).where(Document.run_id == run.id, Document.kind == "spec")
                                .order_by(Document.version.desc())).all()
    data = next((d.data for d in documents if d.data.get("goal")), None)
    if data:
        return Spec.model_validate({k: v for k, v in data.items() if k != "revision"})
    return Spec(goal=run.title, user_stories=[], acceptance_criteria=parse_criteria(documents[0].content))


def write_spec_files(session: Session, run: Run, document) -> str | None:
    """Accepted spec: the story's sections in spec.md and the requirements checklist, sdlc.yaml, the SRS link."""
    ref = feature_ref(session, run)
    if ref is None or not run.commit_artifacts:
        return None
    worktree = worktree_for(session.get(Project, run.project_id), run)
    folder = ref.folder
    model = _spec_model(session, run)
    checks = checklists.requirement_statuses([c.model_dump() for c in model.self_check])
    files = {
        f"{folder}/spec.md": upsert_section(worktree.read(f"{folder}/spec.md"), spec_header(ref), ref.story_id,
                                            spec_section(document.content)),
        f"{folder}/checklists/requirements.md": upsert_section(
            worktree.read(f"{folder}/checklists/requirements.md"), requirements_header(ref), ref.story_id,
            requirements_section(ref, model, checks)),
        f"{folder}/sdlc.yaml": sdlc_yaml(worktree.read(f"{folder}/sdlc.yaml"), ref),
    }
    srs = link_once(worktree.read(SRS_PATH), srs_feature_heading(ref), "Spec", f"{folder}/spec.md")
    if srs:
        files[SRS_PATH] = srs
    return worktree.commit_files(files, f"sdlc: {ref.story_id} spec v{document.version} accepted")


def write_plan_files(session: Session, run: Run, plan) -> str | None:
    """Approved plan: the story's sections in plan.md, tasks.md and test_cases.md, and the SDS plan link."""
    ref = feature_ref(session, run)
    if ref is None or not run.commit_artifacts:
        return None
    worktree = worktree_for(session.get(Project, run.project_id), run)
    folder = ref.folder
    payload, tests = PlanPayload.model_validate(plan.payload), TestPlan.model_validate(plan.test_plan)
    criteria = parse_criteria(latest_document(session, run.id, "spec").content)
    existing_cases = worktree.read(f"{folder}/test_cases.md")
    numbers = tc_numbers(tests, existing_cases, ref.story_id)
    store_artifact(session, run, "tc_numbers", TeamRole.COORDINATOR, json.dumps(numbers))
    files = {
        f"{folder}/plan.md": upsert_section(worktree.read(f"{folder}/plan.md"), plan_header(ref), ref.story_id,
                                            plan_section(ref, payload, criteria)),
        f"{folder}/tasks.md": _tasks_file(worktree, ref, payload, tests, numbers),
        f"{folder}/test_cases.md": upsert_section(existing_cases, test_cases_header(ref), ref.story_id,
                                                  test_cases_section(ref, tests, criteria, numbers)),
    }
    sds = worktree.read(SDS_PATH)
    heading = sds_feature_heading(ref, sds or "")
    linked = link_once(sds, heading, "Plan", f"{folder}/plan.md") if heading else None
    if linked:
        files[SDS_PATH] = linked
    return worktree.commit_files(files, f"sdlc: {ref.story_id} plan v{plan.version} approved")


def _tasks_file(worktree, ref: FeatureRef, payload: PlanPayload, tests: TestPlan, numbers: dict[str, str],
                passed: frozenset[str] = frozenset()) -> str:
    existing = worktree.read(f"{ref.folder}/tasks.md")
    section = tasks_section(ref, payload, tests, existing, phase_number(existing, ref.story_id), passed, numbers)
    return upsert_section(existing, tasks_header(ref), ref.story_id, section, heading=PHASE_HEADING)


def tick_tasks(session: Session, run: Run) -> None:
    """Before the PR: tick the tasks in tasks.md that passed validation."""
    ref, plan = feature_ref(session, run), latest_plan(session, run.id)
    if ref is None or plan is None or not run.commit_artifacts:
        return
    worktree = worktree_for(session.get(Project, run.project_id), run)
    passed = frozenset(t.external_task_id for t in current_tasks(session, run) if t.status == TaskStatus.PASSED)
    numbers = json.loads(artifact(session, run.id, "tc_numbers") or "{}")
    content = _tasks_file(worktree, ref, PlanPayload.model_validate(plan.payload), TestPlan.model_validate(plan.test_plan),
                          numbers, passed)
    worktree.commit_files({f"{ref.folder}/tasks.md": content}, f"sdlc: {ref.story_id} tasks done")


# ---------------------------------------------------------------- gate checklists

GATE_OF_STATUS = {RunStatus.WAITING_BASELINE_APPROVAL: "baseline", RunStatus.WAITING_OPTIONS_APPROVAL: "options", RunStatus.WAITING_PLAN_APPROVAL: "plan", RunStatus.WAITING_TEST_APPROVAL: "tests",
                  RunStatus.WAITING_PR_APPROVAL: "pr"}


def gate_of(run: Run) -> str | None:
    if run.status == RunStatus.WAITING_SPEC_APPROVAL:
        return spec_kind(run)  # spec, srs or sds
    return GATE_OF_STATUS.get(RunStatus(run.status))


def evaluate_gate(session: Session, run: Run, gate: str | None = None) -> dict | None:
    """The checklist of the gate the run waits at: controller results, the role's self-check, the human items."""
    gate = gate or gate_of(run)
    if gate is None:
        return None
    raw_policy = artifact(session, run.id, "effective_policy")
    test_paths = EffectivePolicy.model_validate_json(raw_policy).test_paths if raw_policy else []
    controller: list[dict] = []
    ai: list[dict] = []
    spec = latest_document(session, run.id, "spec")
    if gate == "baseline":
        controller, ai = baseline_checks(session, run)
    elif gate == "options":
        document = latest_document(session, run.id, "options")
        platforms = options_platforms(EffectivePolicy.model_validate_json(raw_policy), _deployment_context(session, run)) \
            if raw_policy else None
        problems = check_options(stored_options(document.data), json.loads(artifact(session, run.id, "baseline") or "null"),
                                 platforms, open_hosting=document.data.get("hosting_basis") == "assumed")
        controller = [checklists._result("compared", "Every option in the baseline is compared, the recommendation is one "
                                         "of the options, and each option has hosting variants on the project's "
                                         "platforms (on-premise among them when the brief leaves hosting open) with "
                                         "one recommended", problems)]
    elif gate == "srs":
        content = latest_document(session, run.id, "srs").content
        problems = check_srs_markdown(content)
        controller = [checklists._result("outline", "SRS sections 1-8 are present and story ids are unique", problems)]
        baseline = json.loads(artifact(session, run.id, "baseline") or "null")
        if baseline:
            controller.append(checklists._result(
                "trace", "Every baseline item in scope is used by an FR, a story or a section, and no unknown REQ is cited",
                check_srs_trace(content, baseline)))
    elif gate == "sds":
        srs_text = mirror_for(session.get(Project, run.project_id)).read_file(run.base_commit, SRS_PATH)
        content = latest_document(session, run.id, "sds").content
        problems = check_sds_markdown(content, srs_text)
        controller = [checklists._result("outline", "SDS sections are present and every SRS story has a section 5 entry",
                                         problems),
                      checklists._result("depth", "The design has its diagrams (context, deployment, data, every SRS "
                                         "flow and lifecycle, sign-in), DTOs and API specifications", check_sds_depth(content, srs_text))]
        chosen = json.loads(artifact(session, run.id, "chosen_option") or "null")
        if chosen:
            hosting = f", hosted as {chosen['hosting']['id']} {chosen['hosting']['name']}" if chosen.get("hosting") else ""
            controller.append(checklists._result("option", f"Section 9 records the chosen option ({chosen['id']} "
                                                 f"{chosen['name']}{hosting}) and why", check_chosen_option(content, chosen)))
    elif gate == "spec":
        controller = checklists.check_spec(spec.content)
        model = _spec_model(session, run)
        ai = checklists.ai_results(checklists.AI_ITEMS["spec"], [c.model_dump() for c in model.self_check])
    elif gate in ("plan", "tests"):
        plan = latest_plan(session, run.id)
        payload, tests = PlanPayload.model_validate(plan.payload), TestPlan.model_validate(plan.test_plan)
        criteria = parse_criteria(spec.content)
        if gate == "plan":
            controller = checklists.check_plan(payload, criteria, plan.grounding_report, test_paths)
            ai = checklists.ai_results(checklists.AI_ITEMS["plan"], [c.model_dump() for c in payload.self_check])
        else:
            controller = checklists.check_tests(tests, payload, criteria, test_paths)
            ai = checklists.ai_results(checklists.AI_ITEMS["tests"], [c.model_dump() for c in tests.self_check])
    elif gate == "pr":
        statuses = {t.external_task_id: t.status for t in current_tasks(session, run)}
        added, changed = _release_evidence(session, run)
        after = latest_document(session, run.id, "analysis")
        findings = after.data.get("findings", []) if after and after.data.get("stage") == "after" else []
        controller = checklists.check_release(statuses, added, changed, findings)
    return {"gate": gate, "controller": controller, "ai": ai, "human": checklists.human_items(gate)}


def baseline_checks(session: Session, run: Run) -> tuple[list[dict], list[dict]]:
    from controller.app.discovery import baseline, questions as q
    from controller.app.discovery.inventory import Inventory
    latest = latest_document(session, run.id, "inventory")
    data = latest.data if latest else {}
    inventory = Inventory.model_validate({k: data.get(k, [] if k != "coverage" else {}) for k in ("items", "gaps", "coverage")})
    run_questions, run_decisions = q.as_dicts(session, run.id)
    return (baseline.check(inventory, data.get("problems", []), run_questions, run_decisions,
                           data.get("decisions_applied", [])), baseline.advisories(inventory, run_questions))


def _release_evidence(session: Session, run: Run) -> tuple[list[str], list[str]]:
    """Lines the run adds (for the secret scan) and other stories' sections that differ from the base."""
    if not run.worktree_path or not Path(run.worktree_path).is_dir() or not run.base_commit:
        return [], []
    import subprocess
    diff = subprocess.run(["git", "-C", run.worktree_path, "diff", "--unified=0", run.base_commit, "HEAD"],
                          capture_output=True, text=True).stdout
    # Secrets are looked for in what ships: source and configuration. Test fixtures ("Secret123") and the story's
    # documents (examples in criteria) are data, so test paths, specs/ and the run thread are skipped.
    raw_policy = artifact(session, run.id, "effective_policy")
    test_paths = EffectivePolicy.model_validate_json(raw_policy).test_paths if raw_policy else []
    added, current = [], None
    for line in diff.splitlines():
        if line.startswith("+++ "):
            current = line[6:] if line.startswith("+++ b/") else None
        elif line.startswith("+") and current and not matches_any(current, test_paths) \
                and not current.startswith(("specs/", THREAD_DIR + "/")) and current not in (SRS_PATH, SDS_PATH):
            added.append(f"{current}: {line[1:]}")
    ref = feature_ref(session, run)
    changed: list[str] = []
    if ref:
        mirror = mirror_for(session.get(Project, run.project_id))
        worktree = worktree_for(session.get(Project, run.project_id), run)
        for name in ("spec.md", "plan.md", "test_cases.md", "checklists/requirements.md"):
            before = mirror.read_file(run.base_commit, f"{ref.folder}/{name}") or ""
            after = worktree.read(f"{ref.folder}/{name}") or ""
            for story, text in _sections(before).items():
                if story != ref.story_id and _sections(after).get(story) != text:
                    changed.append(f"{ref.folder}/{name}: {story}")
    return added, changed


def _sections(text: str) -> dict[str, str]:
    starts = list(re.finditer(r"^## ([A-Z]{2,4}-US-\d{2}(?:\.\d+)?):", text, re.MULTILINE))
    return {m[1]: text[m.start(): starts[i + 1].start() if i + 1 < len(starts) else len(text)].strip()
            for i, m in enumerate(starts)}


def require_checklist(session: Session, run: Run, checked: list[str] | None) -> dict:
    """Refuse an approval while a controller check fails or a human item is unticked; return what to record."""
    evaluation = evaluate_gate(session, run)
    if evaluation is None:
        return {}
    failing = [f"{c['text']}: {c['detail']}" for c in evaluation["controller"] if not c["ok"]]
    if failing:
        raise ValueError("the controller checks fail; revise or reject instead: " + " | ".join(failing))
    missing = checklists.missing_ticks(evaluation["gate"], checked)
    if missing:
        raise ValueError("tick every review item before approving; missing: " + "; ".join(missing))
    return {**evaluation, "human": [{**h, "checked": True} for h in evaluation["human"]]}


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


def context_files(session: Session, run: Run) -> list[str]:
    project = session.get(Project, run.project_id)
    files = mirror_for(project).list_files(run.base_commit)
    rules = json.loads(artifact(session, run.id, "context_rules") or '{"include": ["**"], "exclude": []}')
    return [f for f in files if matches_any(f, rules["include"]) and not matches_any(f, rules["exclude"])]


def code_bundle(session: Session, run: Run, query_texts: list[str], budget: int):
    project = session.get(Project, run.project_id)
    return build_code_context(mirror_for(project), run.base_commit, context_files(session, run), query_texts, budget)


def build_context(session: Session, run: Run, *, kind: str = "", feedback: list[str] | None = None) -> RunContext:
    policy = EffectivePolicy.model_validate_json(artifact(session, run.id, "effective_policy"))
    spec = latest_document(session, run.id, "spec")
    intent = latest_document(session, run.id, "intent").content
    agents = artifact(session, run.id, "agents_md") or ""
    answers = [(c.question, c.answer) for c in session.scalars(
        select(Clarification).where(Clarification.run_id == run.id, Clarification.answer.is_not(None)))]
    bundle = code_bundle(session, run, [intent, spec.content if spec else "", agents],
                         get_settings().context_char_budget)
    srs = sds = None
    if run.kind in PROJECT_DOCS and run.base_commit:
        mirror = mirror_for(session.get(Project, run.project_id))
        srs, sds = mirror.read_file(run.base_commit, SRS_PATH), mirror.read_file(run.base_commit, SDS_PATH)
        # Rewriting after a rejection or after changes asked on the PR: the role works on its last draft, so it
        # changes what the feedback asks and keeps the rest (and its ids).
        draft = latest_document(session, run.id, PROJECT_DOCS[run.kind][0])
        if draft is not None:
            srs, sds = (draft.content, sds) if run.kind == RunKind.SRS else (srs, draft.content)
    return RunContext(title=run.title, policy=policy, agents_md=agents, review_md=artifact(session, run.id, "review_md"),
                      constitution_md=artifact(session, run.id, "constitution") or "",
                      intent_md=intent, files=bundle.files, spec_md=spec.content if spec else None, srs_md=srs, sds_md=sds,
                      answers=answers, story=_story_context(session, run),
                      repository=bundle.as_input(), feedback=feedback or [], session_id=f"{run.id}:{kind}",
                      run_id=str(run.id), worktree=run.worktree_path or "", base_commit=run.base_commit or "",
                      engines=json.loads(artifact(session, run.id, "engines") or "{}"),
                      baseline=json.loads(artifact(session, run.id, "baseline") or "null"),
                      chosen_option=json.loads(artifact(session, run.id, "chosen_option") or "null"),
                      hosting_platforms=options_platforms(policy, _deployment_context(session, run))
                      if run.kind == RunKind.SDS else [])


def _story_context(session: Session, run: Run) -> dict | None:
    ref = feature_ref(session, run)
    return ref.model_dump() if ref else None


def validation_summary(session: Session, run: Run) -> str:
    """The last validation result of each current task, for the Code Reviewer."""
    lines = []
    for task in current_tasks(session, run):
        execution = session.scalar(select(Execution).where(Execution.task_id == task.id, Execution.kind == "code_task")
                                   .order_by(Execution.created_at.desc()).limit(1))
        steps = [s for s in ((execution.result or {}).get("steps", []) if execution else [])
                 if s["state"] == TaskStatus.VALIDATING]
        lines.append(f"{task.external_task_id}: {steps[-1]['detail'] if steps else 'not validated'}")
    return "\n".join(lines)


def load(session: Session, execution: Execution) -> Loaded:
    run = session.get(Run, execution.run_id)
    loaded = Loaded(execution.id, execution.kind, run.id, run.version)
    if execution.kind in ("preflight", "ground", "release"):
        return loaded
    if execution.kind == "extract":
        return _load_extract(session, run, loaded)
    plan = latest_plan(session, run.id)
    feedback: list[str] = []
    if (execution.kind == "plan" and plan and plan.author_kind == "ai" and plan.regenerated_from is None
            and (plan.grounding_report or {}).get("status") == "fail"):
        feedback = list(plan.grounding_report["findings"])
        loaded.extra["regenerated_from"] = str(plan.id)
    if execution.kind == "specify":
        # Reasons of a rejected draft at the gate, and (SRS, SDS) of changes asked on the pull request.
        asked = (Approval.stage == "spec") & (Approval.decision == "reject")
        if run.kind in PROJECT_DOCS:
            asked = asked | ((Approval.stage == "pr") & (Approval.decision == "request_changes"))
        feedback = [f"Reviewer asked for changes: {reason}" for reason in session.scalars(
            select(Approval.reason).where(Approval.run_id == run.id, asked).order_by(Approval.created_at)) if reason]
    if execution.kind == "plan":
        # A replan the Tech Lead or the QC asked for carries their reasons, besides any grounding findings.
        feedback += [f"{'Tech Lead rejected the plan' if stage == 'plan' else 'QC asked for changes'}: {reason}"
                     for stage, reason in session.execute(
                         select(Approval.stage, Approval.reason).where(
                             Approval.run_id == run.id, Approval.stage.in_(("plan", "tests")),
                             Approval.decision == "reject").order_by(Approval.created_at)) if reason]
    if execution.kind == "options":
        feedback = [f"Solution Architect asked for other options: {reason}" for reason in session.scalars(
            select(Approval.reason).where(Approval.run_id == run.id, Approval.stage == "options",
                                          Approval.decision == "reject").order_by(Approval.created_at)) if reason]
    if execution.kind == "design_tests":
        feedback = [f"QC asked for changes: {reason}" for reason in session.scalars(
            select(Approval.reason).where(Approval.run_id == run.id, Approval.stage == "tests",
                                          Approval.decision == "reject").order_by(Approval.created_at)) if reason]
    loaded.extra["run_kind"] = run.kind
    loaded.context = build_context(session, run, kind=execution.kind, feedback=feedback)
    if plan:
        loaded.plan = PlanPayload.model_validate(plan.payload)
        loaded.tests = TestPlan.model_validate(plan.test_plan)
    if execution.kind == "review" and not get_settings().coding_is_mocked and run.worktree_path:
        loaded.extra["diff"] = changes.run_diff(Path(run.worktree_path), run.base_commit, loaded.context.policy.test_paths,
                                                get_settings().context_char_budget // 2)  # spec, plan and test plan need room too
        loaded.extra["validation"] = validation_summary(session, run)
    if execution.task_id:
        task = session.get(Task, execution.task_id)
        loaded.task_payload, loaded.task_attempt = task.payload, task.attempt_count
        ruling = artifact(session, run.id, f"ruling:{task.external_task_id}")
        if ruling and json.loads(ruling)["attempt"] == task.attempt_count:
            loaded.extra["ruling"] = json.loads(ruling)
    return loaded


# ---------------------------------------------------------------- compute (no database)

def compute(team: Team, loaded: Loaded) -> dict:
    context = loaded.context
    calls: list[dict] = []

    def record() -> None:
        call = getattr(team, "last_call", None)
        if call:
            joblog.note(f"answer accepted from {call.engine} after {call.attempts} attempt(s), "
                        f"{(call.usage or {}).get('seconds', '?')}s")
            calls.append({"engine": call.engine, "prompt_version": call.prompt_version, "attempts": call.attempts,
                          "usage": call.usage})

    result: dict = {}
    run_kind = loaded.extra.get("run_kind")
    doing = {"specify": {RunKind.SRS: "Business Analyst writes the SRS", RunKind.SDS: "Architect writes the SDS"}
             .get(run_kind, "Business Analyst writes the spec"),
             "plan": "Tech Lead writes the plan, then the QA Analyst designs the test cases",
             "design_tests": "QA Analyst redesigns the test cases", "review": "Code Reviewer reviews the change",
             "code_task": "QA Engineer, Validation Gate and Developer work on the task"}
    if loaded.kind in doing:
        joblog.note(doing[loaded.kind])
    if loaded.kind == "extract":
        return _extract(team, loaded)
    if loaded.kind == "options":
        joblog.note("Architect compares the architecture options")
        result = {"options": team.design_options(context).model_dump()}
        record()
    elif loaded.kind == "specify" and loaded.extra.get("run_kind") == RunKind.SRS:
        result = {"srs": team.write_srs(context).model_dump()}
        record()
    elif loaded.kind == "specify" and loaded.extra.get("run_kind") == RunKind.SDS:
        result = {"sds": team.write_sds(context).model_dump()}
        record()
    elif loaded.kind == "specify":
        spec, tidied = tidy_spec(team.specify(context), context.policy)
        result = {"spec": spec.model_dump(), "tidied": tidied}
        record()
    elif loaded.kind == "plan":
        plan, tidied = tidy_plan(team.plan(context), context.policy)
        record()
        joblog.note(f"plan has {len(plan.tasks)} task(s); the QA Analyst designs the test cases")
        # The grounding feedback is the Tech Lead's; the QA Analyst gets the QC's feedback only on a redesign.
        tests, tidied_tests = tidy_tests(team.design_tests(dataclasses.replace(context, feedback=[]), plan), plan)
        record()
        result = {"plan": plan.model_dump(), "tests": tests.model_dump(), "tidied": tidied + tidied_tests,
                  "regenerated_from": loaded.extra.get("regenerated_from"), "feedback": context.feedback}
    elif loaded.kind == "design_tests":
        tests, tidied = tidy_tests(team.design_tests(context, loaded.plan), loaded.plan)
        record()
        result = {"tests": tests.model_dump(), "tidied": tidied, "feedback": context.feedback}
    elif loaded.kind == "code_task":
        steps = _work_task(team, loaded)
        executors = sorted({s["evidence"]["executor"] for s in steps if s["evidence"].get("executor")})
        result = {"steps": steps, "_engine": ",".join(executors) or None}
    elif loaded.kind == "review":
        result = {"review": team.review(context, loaded.plan, loaded.tests, diff=loaded.extra.get("diff", ""),
                                        validation=loaded.extra.get("validation", "")).model_dump()}
        record()
    if context is not None:
        result["_context"] = {"selected": [f["path"] for f in context.repository.get("selected_files", [])],
                              "files": len(context.files)}
    result["_calls"] = calls
    if calls and not result.get("_engine"):
        result["_engine"] = ",".join(sorted({c["engine"] for c in calls}))
    return result


def _work_task(team: Team, loaded: Loaded) -> list[dict]:
    """QA Engineer writes tests, Validation Gate red-checks, Developer implements, Validation Gate validates.

    A failed red check goes back to the QA Engineer once with the result. Validation failures go back to the
    Developer with the failing output, at most limits.max_remediations times (.sdlc/project.yaml).
    """
    task = PlanTask.model_validate(loaded.task_payload)
    context, tests = loaded.context, loaded.tests

    def step(state: TaskStatus, role: TeamRole, result: StepResult) -> dict:
        joblog.note(f"{task.id} {state.value} by {role.value}: {'ok' if result.ok else 'not ok'}; {result.detail[:300]}")
        return {"state": state.value, "role": role.value, "ok": result.ok, "detail": result.detail,
                "dispute": result.dispute, "changed_files": result.changed_files, "evidence": result.evidence}

    ruling = loaded.extra.get("ruling")
    if ruling and ruling["decision"] == "amend_tests":
        feedback = (f"The Developer disputed your tests: {ruling['dispute']}\nThe Tech Lead decided that you update "
                    f"the tests: {ruling['reason']}")
        first = team.write_tests(context, task, tests, feedback=feedback, amend=True)
    else:
        first = team.write_tests(context, task, tests)  # reuses tests already committed for the task
    steps = [step(TaskStatus.WRITING_TESTS, TeamRole.QA_ENGINEER, first)]
    if not steps[-1]["ok"]:
        return steps
    steps.append(step(TaskStatus.RED_CHECK, TeamRole.VALIDATION_GATE, team.red_check(context, task, tests)))
    if not steps[-1]["ok"]:
        steps.append(step(TaskStatus.WRITING_TESTS, TeamRole.QA_ENGINEER,
                          team.write_tests(context, task, tests, feedback=steps[-1]["detail"])))
        if not steps[-1]["ok"]:
            return steps
        steps.append(step(TaskStatus.RED_CHECK, TeamRole.VALIDATION_GATE, team.red_check(context, task, tests)))
        if not steps[-1]["ok"]:
            return steps
    upheld = (f"You disputed a test earlier: {ruling['dispute']}\nThe Tech Lead kept the tests: {ruling['reason']}\n"
              f"Make the code pass them.") if ruling and ruling["decision"] == "keep_tests" else ""
    steps.append(step(TaskStatus.CODING, TeamRole.DEVELOPER, team.implement(context, task, 1, tests, feedback=upheld)))
    max_remediations = context.policy.max_remediations
    for remediation in range(max_remediations + 1):
        if not steps[-1]["ok"] or steps[-1]["dispute"]:
            return steps
        steps.append(step(TaskStatus.VALIDATING, TeamRole.VALIDATION_GATE,
                          team.validate(context, task, attempt=remediation + 1)))
        if steps[-1]["ok"] or remediation == max_remediations:
            return steps
        failure = steps[-1]["evidence"].get("failures") or steps[-1]["detail"]
        steps.append(step(TaskStatus.REMEDIATING, TeamRole.DEVELOPER,
                          team.implement(context, task, remediation + 2, tests, feedback=failure)))
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
    execution.engine = result.get("_engine")  # only jobs that called a model or a CLI name an engine
    calls = result.get("_calls") or []
    if calls:
        execution.prompt_version = ",".join(sorted({c["prompt_version"] for c in calls if c["prompt_version"]}))
    handler = {"preflight": _apply_preflight, "specify": _apply_specify, "plan": _apply_plan,
               "ground": _apply_ground, "design_tests": _apply_design_tests, "code_task": _apply_code_task, "review": _apply_review,
               "release": _apply_release, "extract": _apply_extract, "options": _apply_options}[loaded.kind]
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
    if run.kind == RunKind.SDS and mirror.read_file(base, SRS_PATH) is None:
        finish(session, execution, "failed", error="SRS.md is missing")
        to_needs_human(session, run, f"SRS.md is not on {project.default_branch} at {base[:12]}; "
                                     "merge the SRS pull request first, then resume")
        return
    intent_data = latest_document(session, run.id, "intent").data or {}
    if run.kind in (RunKind.SRS, RunKind.SDS) and (intent_data.get("from_baseline") or run.kind == RunKind.SDS):
        from controller.app.discovery.baseline import BASELINE_JSON
        raw_baseline = mirror.read_file(base, BASELINE_JSON)
        if raw_baseline is None and intent_data.get("from_baseline"):
            finish(session, execution, "failed", error=f"{BASELINE_JSON} is missing")
            to_needs_human(session, run, f"{BASELINE_JSON} is not on {project.default_branch} at {base[:12]}; "
                                         "merge the discovery pull request first, then resume")
            return
        if raw_baseline is not None:
            store_artifact(session, run, "baseline", TeamRole.COORDINATOR, raw_baseline)
    story = intent_data.get("story") if run.kind == RunKind.FEATURE else None
    if story:
        try:
            ref = resolve_feature(mirror.read_file(base, SRS_PATH) or "", mirror.list_files(base),
                                  lambda path: mirror.read_file(base, path), story)
        except ValueError as exc:
            finish(session, execution, "failed", error=str(exc))
            to_needs_human(session, run, f"{exc} at {base[:12]}")
            return
        store_artifact(session, run, "feature", TeamRole.COORDINATOR, ref.model_dump_json())
    policy = effective_policy(snapshot.config)
    store_artifact(session, run, "project_config", TeamRole.COORDINATOR,
                   mirror.read_file(base, ".sdlc/project.yaml"))
    store_artifact(session, run, "effective_policy", TeamRole.COORDINATOR, policy.model_dump_json())
    store_artifact(session, run, "context_rules", TeamRole.COORDINATOR,
                   json.dumps(snapshot.config.context.model_dump()))
    store_artifact(session, run, "agents_md", TeamRole.COORDINATOR, snapshot.agents_md)
    if snapshot.review_md:
        store_artifact(session, run, "review_md", TeamRole.COORDINATOR, snapshot.review_md)
    if snapshot.constitution_md:
        store_artifact(session, run, "constitution", TeamRole.COORDINATOR, snapshot.constitution_md)
    run.base_commit = base
    modes = {**policy.stages.model_dump(), **run.stage_overrides}
    if modes.get("plan") not in ("manual", "gated"):
        modes["plan"] = "gated"  # the plan gate cannot be removed
    run.stage_modes = modes
    run.commit_artifacts = policy.commit_artifacts
    run.branch = f"sdlc/run-{str(run.id)[:8]}"
    run.worktree_path = str(DATA_DIR / "worktrees" / str(run.id))
    if run.commit_artifacts:
        worktree_for(project, run).create(base)
        intent = latest_document(session, run.id, "intent")
        intent.commit_sha = commit_thread(session, run, "intent", intent.content,
                                          f"sdlc: intent for {run.title}")
    if run.kind == RunKind.DISCOVERY:
        transition_run(session, run, RunStatus.INGESTING, actor=COORDINATOR,
                       reason="add the raw requirement sources, then start the extraction")
        return
    if run.kind == RunKind.SDS and modes.get("options", "gated") != "skip":
        transition_run(session, run, RunStatus.DESIGNING_OPTIONS, actor=COORDINATOR,
                       reason="the Architect compares the architecture options before the SDS")
        queue(session, run, "options")
        return
    enter_stage(session, run, "spec")


def _deployment_context(session: Session, run: Run) -> dict:
    intent = latest_document(session, run.id, "intent")
    return (intent.data or {}).get("deployment_context") or {} if intent else {}


def options_platforms(policy: EffectivePolicy, deployment: dict) -> list[str]:
    """The platforms the hosting variants may use: the one the brief states, or the project's list."""
    return [deployment["platform"]] if deployment.get("platform") else list(policy.hosting_platforms)


def _apply_options(session: Session, run: Run, execution: Execution, result: dict, team: Team) -> None:
    options = number_options(Options.model_validate(result["options"]))
    content = render_options(options, title=run.title.removeprefix("SDS: "))
    brief = ArchitectureBrief.model_validate(latest_document(session, run.id, "intent").data)
    # hosting the brief does not state is the Architect's assumption: the gate then asks the SA to choose knowingly
    basis = "stated" if brief.hosting_stated() else "assumed"
    document = add_document(session, run.id, "options", content, author_kind="ai", author=TeamRole.ARCHITECT.value,
                            data={**options.model_dump(), "hosting_basis": basis})
    document.commit_sha = commit_thread(session, run, "options", content, f"sdlc: architecture options v{document.version}")
    transition_run(session, run, RunStatus.WAITING_OPTIONS_APPROVAL, actor=TeamRole.ARCHITECT.value,
                   reason=f"{len(options.options)} options compared; the Architect recommends {options.recommendation}")


# Project documents written before feature runs: document kind, file at the repository root, the AI role.
PROJECT_DOCS: dict[str, tuple[str, str, TeamRole]] = {
    RunKind.SRS: ("srs", SRS_PATH, TeamRole.BUSINESS_ANALYST),
    RunKind.SDS: ("sds", SDS_PATH, TeamRole.ARCHITECT),
}


def spec_kind(run: Run) -> str:
    """The document the first gate reviews: spec.md, or SRS.md / SDS.md for a project document run."""
    return PROJECT_DOCS[run.kind][0] if run.kind in PROJECT_DOCS else "spec"


def _apply_project_doc(session: Session, run: Run, result: dict) -> None:
    kind, path, role = PROJECT_DOCS[run.kind]
    srs_text = None
    if run.kind == RunKind.SRS:
        model = Srs.model_validate(result["srs"])
        baseline = json.loads(artifact(session, run.id, "baseline") or "null")
        content, findings = render_srs(model, author="Business Analyst (AI)", baseline=baseline), []
    else:
        model = Sds.model_validate(result["sds"])
        srs_text = mirror_for(session.get(Project, run.project_id)).read_file(run.base_commit, SRS_PATH) or ""
        product = re.sub(r"\s*-\s*Software Requirements Specification.*$", "",
                         (srs_text.splitlines() or ["# Product"])[0].lstrip("# ").strip()) or run.title
        content = render_sds(model, srs_features(srs_text), product=product, author="Architect (AI)")
        findings = sds_sync_findings(model, srs_text)
    blocking = [q for q in model.questions if q.blocking]
    if blocking:
        for question in blocking:
            session.add(Clarification(run_id=run.id, question=question.question, blocking=True))
        transition_run(session, run, RunStatus.NEEDS_CLARIFICATION, actor=role.value,
                       reason=f"{len(blocking)} blocking question(s) for the Product Owner")
        return
    document = add_document(session, run.id, kind, content, author_kind="ai", author=role.value,
                            data={**result[kind], "sync_findings": findings,
                                  "revision": _revision(session, run, kind, content, model.feedback_replies)})
    # A project document gate cannot be skipped: the customer signs the SRS, the architect owns the SDS.
    transition_run(session, run, RunStatus.WAITING_SPEC_APPROVAL, actor=role.value,
                   reason=f"{path} v{document.version} ready" + (f"; {len(findings)} sync finding(s)" if findings else ""))


def _revision(session: Session, run: Run, kind: str, content: str, replies: list) -> dict | None:
    """A rewrite after the reviewer rejected the last version (or asked for changes on the PR): what they asked,
    how the role says it handled each point, and which sections changed, so the next gate can check it."""
    previous = latest_document(session, run.id, kind)
    if previous is None:
        return None
    asked = session.scalars(select(Approval.reason).where(
        Approval.run_id == run.id, Approval.created_at >= previous.created_at, Approval.reason.is_not(None),
        ((Approval.stage == "spec") & (Approval.decision == "reject"))
        | ((Approval.stage == "pr") & (Approval.decision == "request_changes"))).order_by(Approval.created_at)).all()
    return {"from_version": previous.version, "feedback": [a for a in asked if a.strip()],
            "replies": [r.model_dump() for r in replies], "sections": section_changes(previous.content, content)}


def _apply_specify(session: Session, run: Run, execution: Execution, result: dict, team: Team) -> None:
    if run.kind in PROJECT_DOCS:
        _apply_project_doc(session, run, result)
        return
    spec = Spec.model_validate(result["spec"])
    blocking = [q for q in spec.questions if q.blocking]
    if blocking:
        for question in blocking:
            session.add(Clarification(run_id=run.id, question=question.question, blocking=True))
        transition_run(session, run, RunStatus.NEEDS_CLARIFICATION, actor=TeamRole.BUSINESS_ANALYST.value,
                       reason=f"{len(blocking)} blocking question(s) for the Product Owner")
        return
    ref = feature_ref(session, run)
    content = render_spec(run.title, spec, story=ref.story() if ref else None)
    document = add_document(session, run.id, "spec", content, author_kind="ai", author=TeamRole.BUSINESS_ANALYST.value,
                            data={**result["spec"], "revision": _revision(session, run, "spec", content, spec.feedback_replies)})
    if stage_mode(run, "spec") == "auto":
        accept_spec(session, run, document, actor=COORDINATOR)
    else:
        transition_run(session, run, RunStatus.WAITING_SPEC_APPROVAL, actor=TeamRole.BUSINESS_ANALYST.value,
                       reason=f"spec v{document.version} ready")


def accept_spec(session: Session, run: Run, document, *, actor: str, acting_role: str | None = None) -> None:
    if run.kind in PROJECT_DOCS:
        # SRS.md or SDS.md goes to the repository root through a draft PR; no plan or code follows.
        path = PROJECT_DOCS[run.kind][1]
        if run.commit_artifacts:
            project = session.get(Project, run.project_id)
            document.commit_sha = worktree_for(project, run).commit_file(
                path, document.content, f"sdlc: {path} v{document.version} accepted")
        transition_run(session, run, RunStatus.PR_OPEN, actor=actor, acting_role=acting_role,
                       reason=f"{path} v{document.version} accepted")
        queue(session, run, "release")
        return
    document.commit_sha = commit_thread(session, run, "spec", document.content,
                                        f"sdlc: spec v{document.version} accepted")
    document.commit_sha = write_spec_files(session, run, document) or document.commit_sha
    enter_stage(session, run, "plan", actor=actor, acting_role=acting_role)


def _apply_plan(session: Session, run: Run, execution: Execution, result: dict, team: Team) -> None:
    plan = add_plan(session, run.id, result["plan"], result["tests"], author_kind="ai",
                    author=TeamRole.TECH_LEAD.value)
    if result.get("regenerated_from"):
        plan.regenerated_from = uuid.UUID(result["regenerated_from"])
    record_event(session, "plan.proposed", actor=TeamRole.TECH_LEAD.value, run_id=run.id,
                 payload={"plan": str(plan.id), "version": plan.version,
                          "test_cases": len(result["tests"]["cases"]), "qa_analyst": team.engine,
                          "tidied": result.get("tidied", [])})
    transition_run(session, run, RunStatus.GROUNDING, actor=TeamRole.TECH_LEAD.value)
    queue(session, run, "ground")


def ground(session: Session, run: Run, plan) -> dict:
    """Run the deterministic grounding check on a plan version and store the report on it."""
    policy = EffectivePolicy.model_validate_json(artifact(session, run.id, "effective_policy"))
    spec = latest_document(session, run.id, "spec")
    bundle = code_bundle(session, run, [], budget=0)
    report = check_with_bundle(PlanPayload.model_validate(plan.payload), TestPlan.model_validate(plan.test_plan),
                               parse_criteria(spec.content), policy, bundle)
    plan.grounding_report = report
    record_event(session, "plan.grounded", actor=COORDINATOR, run_id=run.id,
                 payload={"plan": str(plan.id), "version": plan.version, "status": report["status"],
                          "findings": len(report["findings"])})
    return report


def _apply_ground(session: Session, run: Run, execution: Execution, result: dict, team: Team) -> None:
    plan = latest_plan(session, run.id)
    report = ground(session, run, plan)
    if report["status"] == "fail" and plan.author_kind == "ai" and plan.regenerated_from is None:
        transition_run(session, run, RunStatus.PLANNING, actor=COORDINATOR,
                       reason=f"grounding found {len(report['findings'])} problem(s); the Tech Lead replans once")
        queue(session, run, "plan", key=f"regen:{plan.id}")
        return
    note = "passed" if report["status"] == "pass" else f"failed with {len(report['findings'])} finding(s)"
    transition_run(session, run, RunStatus.WAITING_PLAN_APPROVAL, actor=COORDINATOR,
                   reason=f"plan v{plan.version} ready; grounding {note}")


def tests_markdown(session: Session, run: Run, plan) -> str:
    """The plan version's test cases as the story's section of test_cases.md, for the Quality gate."""
    ref = feature_ref(session, run) or FeatureRef(
        story_id="", story_title=run.title, actors="", statement="", srs_section="", feature_section="",
        feature_name=run.title, prefix="", feature_id="", slug="")
    existing = None
    if ref.story_id and run.commit_artifacts and run.worktree_path:
        existing = worktree_for(session.get(Project, run.project_id), run).read(f"{ref.folder}/test_cases.md")
    tests = TestPlan.model_validate(plan.test_plan)
    criteria = parse_criteria(latest_document(session, run.id, "spec").content)
    return test_cases_section(ref, tests, criteria, tc_numbers(tests, existing, ref.story_id))


def enter_quality_gate(session: Session, run: Run, plan, *, actor: str, acting_role: str | None = None) -> None:
    """The QC reviews the test cases of this plan version before any code is written."""
    document = add_tests_document(session, run, plan)
    transition_run(session, run, RunStatus.WAITING_TEST_APPROVAL, actor=actor, acting_role=acting_role,
                   reason=f"test cases v{document.version} ready for QC")


def add_tests_document(session: Session, run: Run, plan):
    report = plan.grounding_report or {}
    return add_document(session, run.id, "tests", tests_markdown(session, run, plan), author_kind=plan.author_kind,
                            author=plan.author if plan.author_kind == "human" else TeamRole.QA_ANALYST.value,
                            data={"plan_id": str(plan.id), "plan_version": plan.version,
                                  "sync_findings": [*report.get("findings", []), *report.get("warnings", [])]})


def _apply_design_tests(session: Session, run: Run, execution: Execution, result: dict, team: Team) -> None:
    """The QA Analyst redesigned the tests after QC asked for changes: same plan, new test plan version."""
    approved = latest_plan(session, run.id)
    plan = add_plan(session, run.id, approved.payload, result["tests"], author_kind="ai",
                    author=TeamRole.QA_ANALYST.value)
    ground(session, run, plan)
    enter_quality_gate(session, run, plan, actor=TeamRole.QA_ANALYST.value)


def plan_markdown(run: Run, plan) -> str:
    return render_plan(run.title, plan.version, PlanPayload.model_validate(plan.payload),
                       TestPlan.model_validate(plan.test_plan), plan.grounding_report)


def _apply_code_task(session: Session, run: Run, execution: Execution, result: dict, team: Team) -> None:
    task = session.get(Task, execution.task_id, with_for_update=True)
    if task.status != TaskStatus.WRITING_TESTS:
        finish(session, execution, "discarded", error=f"task is {task.status}")
        return
    for index, step in enumerate(result["steps"]):
        final = index == len(result["steps"]) - 1
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
        if not step["ok"] and final and state in (TaskStatus.WRITING_TESTS, TaskStatus.RED_CHECK, TaskStatus.CODING,
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
        policy = EffectivePolicy.model_validate_json(artifact(session, run.id, "effective_policy"))
        to_needs_human(session, run, f"{task.external_task_id}: validation failed after {policy.max_remediations} "
                                     "remediations")


def _apply_review(session: Session, run: Run, execution: Execution, result: dict, team: Team) -> None:
    review = result["review"]
    engine = result.get("_engine") or team.engine  # the engine that answered, e.g. claude-readonly
    markdown = review.get("markdown") or render_review(run.title, Review.model_validate(review), engine)
    document = add_document(session, run.id, "review", markdown, author_kind="ai",
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
    for kind in ("intent", "spec", "srs", "sds", "tests", "analysis", "review"):
        document = latest_document(session, run.id, kind)
        if document:
            name = {"srs": SRS_PATH, "sds": SDS_PATH}.get(kind, f"{kind}.md")
            lines.append(f"| {name} | v{document.version} | `{document.sha256[:16]}` | {document.author} |")
    plan = latest_plan(session, run.id)
    if plan:
        lines.append(f"| plan.md | v{plan.version} | `{plan.sha256[:16]}` | {plan.author} |")
    lines += ["", "## Tasks", "", "| Task | Status | Attempts |", "|---|---|---|"]
    for task in current_tasks(session, run):
        lines.append(f"| {task.external_task_id} {task.payload['title']} | {task.status} | {task.attempt_count} |")
    rulings = [json.loads(a) for a in session.scalars(select(Artifact.content).where(
        Artifact.run_id == run.id, Artifact.kind.startswith("ruling:")).order_by(Artifact.created_at))]
    if rulings:
        lines += ["", "## Test disputes decided by a human", ""]
        for r in rulings:
            verdict = "QA Engineer updated the tests" if r["decision"] == "amend_tests" else "tests kept"
            lines.append(f"- {r['task']}: {verdict} ({r['actor']}): {r['reason']}. Dispute: {r['dispute']}")
    amended = [f"{session.get(Task, e.task_id).external_task_id}: {', '.join(step['evidence']['amended'])}"
               for e in session.scalars(select(Execution).where(Execution.run_id == run.id,
                                                                Execution.kind == "code_task"))
               for step in (e.result or {}).get("steps", []) if (step.get("evidence") or {}).get("amended")]
    if amended:
        lines += ["", "Existing test lines changed (review these):", "", *[f"- {a}" for a in amended]]
    engines = sorted({e for e in session.scalars(select(Execution.engine).where(Execution.run_id == run.id)) if e})
    lines += ["", f"Team engine(s): {', '.join(engines) or 'none'}", ""]
    return "\n".join(lines)


def record_analysis(session: Session, run: Run, stage: str) -> None:
    """Store the cross-artifact analysis as a versioned document in the run's thread."""
    plan = latest_plan(session, run.id)
    if plan is None:  # SRS and SDS runs have no plan, tasks or tests
        return
    payload, tests = PlanPayload.model_validate(plan.payload), TestPlan.model_validate(plan.test_plan)
    if stage == "before":
        findings = analysis.before_coding(parse_criteria(latest_document(session, run.id, "spec").content), payload, tests)
    else:
        statuses = {t.external_task_id: t.status for t in current_tasks(session, run)}
        if get_settings().coding_is_mocked or not run.worktree_path:
            findings = analysis.after_coding(payload, tests, statuses, [p for t in payload.tasks for p in [*t.modify, *t.create]]
                                             + [c.target_file for c in tests.cases], [])  # simulated coding changes nothing
        else:
            import subprocess
            changed = subprocess.run(["git", "-C", run.worktree_path, "diff", "--name-only", run.base_commit, "HEAD"],
                                     capture_output=True, text=True).stdout.split()
            raw_policy = artifact(session, run.id, "effective_policy")
            test_paths = EffectivePolicy.model_validate_json(raw_policy).test_paths if raw_policy else []
            findings = analysis.after_coding(payload, tests, statuses, changed, test_paths)
    document = add_document(session, run.id, "analysis", analysis.render(run.title, stage, findings), author_kind="ai",
                            author=TeamRole.COORDINATOR.value, data={"stage": stage, "findings": findings})
    document.commit_sha = commit_thread(session, run, f"analysis-{stage}", document.content,
                                        f"sdlc: analysis {stage} coding")


def _apply_release(session: Session, run: Run, execution: Execution, result: dict, team: Team) -> None:
    tick_tasks(session, run)
    record_analysis(session, run, "after")
    report = add_document(session, run.id, "report", execution_report(session, run), author_kind="ai",
                          author=TeamRole.RELEASE_ENGINEER.value)
    head = commit_thread(session, run, "report", report.content, "sdlc: execution report")
    report.commit_sha = head
    project = session.get(Project, run.project_id)
    slug = None if get_settings().coding_is_mocked else repo_slug(project.repo_url)
    if slug:
        worktree = worktree_for(project, run)
        head = changes.head(worktree.path)
        github.push(worktree.path, run.branch)
        number, url = github.open_draft(slug, project.default_branch, run.branch, f"[SDLC] {run.title}", report.content)
        release = ReleaseResult(url=url, number=number, simulated=False)
    else:
        release = team.open_pull_request(build_context(session, run), run.branch)
    pull = session.scalar(select(PullRequest).where(PullRequest.run_id == run.id))
    if pull is None:  # after requested changes the same pull request gets a new head
        pull = PullRequest(run_id=run.id)
        session.add(pull)
    pull.number, pull.url, pull.head_sha, pull.simulated = release.number, release.url, head, release.simulated
    pull.ci_status, pull.review_status = ("simulated" if release.simulated else "pending"), "pending"
    transition_run(session, run, RunStatus.WAITING_PR_APPROVAL, actor=TeamRole.RELEASE_ENGINEER.value,
                   reason="simulated pull request: the branch exists only in the local mirror"
                   if release.simulated else f"draft PR {release.url}")


def recheck_after_merge(session: Session, project: Project) -> None:
    """A merge changes what the next runs build on (SRS.md, SDS.md, requirements/, code): check the project again at
    once, so the Projects page and the story list of a feature request show it without a Check again."""
    from controller.app.projects.registry import check_project
    try:
        check_project(session, project, actor="github")
    except Exception as exc:  # the next run start checks again anyway
        log.warning("project %s: check after a merge failed: %s", project.key, exc)


def poll_pull_requests(session: Session) -> None:
    """Read CI, review and merge state of open draft PRs; a merge of the exact pushed head completes the run."""
    rows = session.execute(select(Run, PullRequest, Project).join(PullRequest, PullRequest.run_id == Run.id)
                           .join(Project, Project.id == Run.project_id)
                           .where(Run.status == RunStatus.WAITING_PR_APPROVAL, PullRequest.simulated.is_(False),
                                  PullRequest.number.is_not(None))).all()
    for run, pull, project in rows:
        slug = repo_slug(project.repo_url)
        if not slug:
            continue
        try:
            state = github.pull_state(slug, pull.number)
        except github.GitHubError as exc:
            log.warning("cannot read PR %s#%s: %s", slug, pull.number, exc)
            continue
        session.refresh(run, with_for_update=True)
        if run.status != RunStatus.WAITING_PR_APPROVAL:
            continue
        if (state.ci_status, state.review_status) != (pull.ci_status, pull.review_status):
            record_event(session, "pr.updated", actor=TeamRole.RELEASE_ENGINEER.value, run_id=run.id,
                         payload={"number": pull.number, "ci": state.ci_status, "review": state.review_status})
            pull.ci_status, pull.review_status = state.ci_status, state.review_status
        if state.state == "MERGED" and state.head_sha == pull.head_sha:
            pull.merged_sha = state.merged_sha
            transition_run(session, run, RunStatus.COMPLETED, actor="github", acting_role="code_owner",
                           reason=f"PR #{pull.number} merged as {state.merged_sha} (head {state.head_sha[:12]})")
            recheck_after_merge(session, project)
        elif state.state == "MERGED":
            to_needs_human(session, run, f"PR #{pull.number} was merged with head {state.head_sha[:12]}, "
                                         f"not the head the team pushed ({(pull.head_sha or '')[:12]})")
        elif state.state == "CLOSED":
            pull.review_status = "closed"
            transition_run(session, run, RunStatus.CLOSED_UNMERGED, actor="github", acting_role="code_owner",
                           reason=f"PR #{pull.number} closed without merge")


# ---------------------------------------------------------------- discovery: extraction

def _load_extract(session: Session, run: Run, loaded: Loaded) -> Loaded:
    """What this round reads: sources not read in an earlier round (the others are reused), minutes not read yet,
    the open questions and the accepted decisions."""
    from controller.app.discovery import questions as q
    from controller.app.discovery.extract import plan_parts
    from controller.app.discovery.service import discovery_config, source_dir, sources
    limits = discovery_config(session, run).limits
    previous = latest_document(session, run.id, "inventory")
    data = dict(previous.data) if previous else {}
    if previous and "chunks" not in data:
        data["chunks"] = _chunks_from(data)
    reused, parts, minutes = {}, [], []
    engines = json.loads(artifact(session, run.id, "engines") or "{}")
    reads_files = engines.get("business_analyst", get_settings().sdlc_default_engine) == "claude-readonly"
    for source in sources(session, run.id):
        if source.purpose == "requirements" and str(source.id) in data.get("chunks", {}):
            reused[str(source.id)] = data["chunks"][str(source.id)]
            continue
        if source.purpose == "minutes" and str(source.id) in data.get("minutes_read", []):
            continue
        text, kind = None, source.kind
        if source.kind == "pdf" and not reads_files:
            text, kind = _pdf_text_view(source), "pdf-text"
        elif source.kind == "image" and not reads_files:
            raise JobAborted(f"{source.filename} is an image; only claude-readonly reads images. Remove it, or put the "
                             "discovery's Business Analyst on claude-readonly")
        elif source.kind not in ("pdf", "image"):
            text = Path(source.text_path or source.path).read_text(errors="replace")
        planned = plan_parts(str(source.id), source.filename, kind, source.path, source.pages, text,
                             pages_per_read=limits.pages_per_read, chars_per_read=limits.chars_per_read)
        (minutes if source.purpose == "minutes" else parts).extend(planned)
    run_questions = q.questions(session, run.id)
    accepted = [q.decision_id(d) for d in q.decisions(session, run.id) if d.status == "accepted"]
    new_decisions = [d for d in accepted if d not in data.get("decisions_applied", [])]
    removed = [sid for sid in data.get("chunks", {}) if sid not in {str(s.id) for s in sources(session, run.id)}]
    new_input = ([f"{len({p.source_id for p in parts})} new source(s)"] if parts else []) \
        + ([f"{len({p.source_id for p in minutes})} new minutes"] if minutes else []) \
        + ([f"new decisions {', '.join(new_decisions)}"] if new_decisions else []) \
        + ([f"{len(removed)} removed source(s)"] if removed else [])
    loaded.extra.update({
        "round": (previous.version if previous else 0) + 1, "new_input": new_input if previous else ["first round"],
        "parts": parts, "minutes": minutes, "reused": reused, "run_kind": run.kind,
        "previous": {k: data.get(k, []) for k in ("items", "gaps")} if previous else None,
        "previous_parts": [p for p in data.get("parts", []) if p["source"] in reused],
        "minutes_read": data.get("minutes_read", []),
        "questions": [{"id": q.number(run_questions, x), "kind": x.kind, "question": x.question, "items": x.items or []}
                      for x in run_questions if x.status == q.OPEN],
        "asked": [{"id": q.number(run_questions, x), "question": x.question, "status": x.status,
                   "needs_suggestion": x.status == q.OPEN and not q.useful_suggestion(x.suggestion, x.question)}
                  for x in run_questions],
        "decisions": [{"id": q.decision_id(d), "text": d.text, "items": d.items}
                      for d in q.decisions(session, run.id) if d.status == "accepted"],
        "effects": [{"id": q.decision_id(d), "items": d.items, "effect": d.effect, "new_text": d.new_text or ""}
                    for d in q.decisions(session, run.id) if d.status == "accepted" and d.effect]})
    loaded.context = RunContext(
        title=run.title, policy=EffectivePolicy.model_validate_json(artifact(session, run.id, "effective_policy")),
        agents_md=artifact(session, run.id, "agents_md") or "", review_md=None,
        intent_md=latest_document(session, run.id, "intent").content, files=[], session_id=f"{run.id}:extract",
        run_id=str(run.id), base_commit=run.base_commit or "", engines=json.loads(artifact(session, run.id, "engines") or "{}"),
        sources_dir=str(source_dir(session.get(Project, run.project_id), run)))
    return loaded


def _pdf_text_view(source) -> str:
    """The PDF's text with [page N] markers, made once with pdftotext and kept next to the file."""
    from controller.app.discovery.sources import pdf_text
    if source.text_path and Path(source.text_path).is_file():
        return Path(source.text_path).read_text(errors="replace")
    settings = get_settings()
    text = pdf_text(Path(source.path), tool=settings.pdftotext_path, timeout=settings.pdftotext_timeout_seconds)
    if not text:
        raise JobAborted(f"{source.filename}: no text could be read with {settings.pdftotext_path} (not installed, "
                         "or a scanned PDF without a text layer); only claude-readonly reads such a PDF")
    target = Path(source.path).with_name(Path(source.path).name + ".md")
    target.write_text(text)
    source.text_path = str(target)
    return text


def _chunks_from(data: dict) -> dict[str, list[dict]]:
    """An inventory written before rounds kept what each source gave: rebuild it from the merged items (each under
    the first source it cites) so the next round reuses them instead of reading every file again."""
    chunks: dict[str, dict] = {}
    for item in data.get("items", []):
        first = next((c["source"] for c in item["sources"] if not c["source"].startswith("decision:")), None)
        if first:
            chunks.setdefault(first, {"items": [], "gaps": [], "read": data.get("coverage", {}).get(first, [])})
            chunks[first]["items"].append({k: v for k, v in item.items() if k != "id"})
    if chunks:
        next(iter(chunks.values()))["gaps"] = data.get("gaps", [])
    return {sid: [chunk] for sid, chunk in chunks.items()}


def _read_parts(team: Team, context: RunContext, parts: list, read, record, what: str) -> list[tuple]:
    """Each part in turn; a part whose answer is unusable is halved and read again instead of retried at the same size."""
    from controller.app.discovery.extract import split
    from controller.app.integrations.langflow import InvalidModelOutput
    done, pending, index = [], list(parts), 0
    while pending:
        part, index = pending.pop(0), index + 1
        halves = split(part)
        joblog.note(f"Business Analyst reads {what} {part.filename}, {part.label} (part {index}, {len(pending)} more after it)")
        # A part that can be halved gets no format retries: a too-long answer fails again at the same size.
        policy = context.policy.model_copy(update={"max_format_retries": 0}) if halves else context.policy
        part_context = dataclasses.replace(context, policy=policy, session_id=f"{context.session_id}:{what}:{index}")
        try:
            result = read(part_context, part)
        except InvalidModelOutput as exc:
            if not halves:
                raise
            joblog.note(f"  answer unusable ({str(exc)[:160]}); reading {halves[0].label} and {halves[1].label} instead")
            pending[:0] = halves
            continue
        record()
        done.append((part, result))
    return done


def _extract(team: Team, loaded: Loaded) -> dict:
    """New sources part by part, new minutes, then one consolidation pass over the merged inventory."""
    from controller.app.discovery.inventory import (ChunkResult, Inventory, MinutesResult, Update, apply_consolidation,
                                                    merge_chunks)
    from controller.app.integrations.langflow import prompt_text
    context, extra = loaded.context, loaded.extra
    calls: list[dict] = []
    reused = [False]  # the step just done came from an earlier attempt: no new call to record

    def record() -> None:
        call = getattr(team, "last_call", None)
        if reused[0]:
            reused[0] = False
        elif call:
            calls.append({"engine": call.engine, "prompt_version": call.prompt_version, "attempts": call.attempts,
                          "usage": call.usage})

    if extra["reused"]:
        joblog.note(f"reusing what earlier rounds read of {len(extra['reused'])} source(s)")
    known: dict[str, list[str]] = {}
    chunks = [(sid, ChunkResult.model_validate(c)) for sid, stored in extra["reused"].items() for c in stored]

    def read_part(part_context, part):
        snapshot = {kind: list(names) for kind, names in known.items()}
        chunk, reused[0] = joblog.resumable(
            f"extract {part.source_id} {part.label}",
            {"source": part.source_id, "part": part.label, "known": snapshot, "prompt": prompt_text("extract")},
            lambda: team.extract_part(dataclasses.replace(part_context, repository={"known": snapshot}), part),
            ChunkResult.model_validate)
        joblog.note(f"  {len(chunk.items)} item(s), {len(chunk.gaps)} question(s)")
        for item in chunk.items:  # later parts reuse the names of earlier ones
            if item.type in ("actor", "feature", "term", "data") and item.name not in known.setdefault(item.type, []):
                known[item.type].append(item.name)
        return chunk
    for _, chunk in chunks:
        for item in chunk.items:
            if item.type in ("actor", "feature", "term", "data") and item.name not in known.setdefault(item.type, []):
                known[item.type].append(item.name)
    fresh = _read_parts(team, context, extra["parts"], read_part, record, "")
    chunks += [(part.source_id, chunk) for part, chunk in fresh]
    stored: dict[str, list] = {sid: list(c) for sid, c in extra["reused"].items()}
    for part, chunk in fresh:
        stored.setdefault(part.source_id, []).append(chunk.model_dump())
    previous = Inventory.model_validate({**extra["previous"], "coverage": {}}) if extra["previous"] else None
    inventory = merge_chunks(chunks, previous)
    dumped = inventory.model_dump()

    def read_minutes(part_context, part):
        result, reused[0] = joblog.resumable(
            f"minutes {part.source_id} {part.label}",
            {"source": part.source_id, "part": part.label, "questions": extra["questions"], "items": dumped["items"],
             "prompt": prompt_text("minutes")},
            lambda: team.read_minutes(part_context, part, extra["questions"], dumped["items"]),
            MinutesResult.model_validate)
        return result
    minutes = _read_parts(team, context, extra["minutes"], read_minutes, record, "minutes")
    joblog.note(f"{len(inventory.items)} item(s), {len(extra['decisions'])} accepted decision(s); "
                "the Business Analyst consolidates")
    if inventory.items:
        consolidation = team.consolidate(context, dumped["items"], dumped["gaps"], extra["decisions"], extra["asked"],
                                         {"round": extra["round"], "new_input": extra["new_input"]})
        record()
        if not extra["new_input"]:  # check again: nothing new to read, so only a conflict is worth a question
            consolidation = consolidation.model_copy(update={"gaps": [g for g in consolidation.gaps if g.kind == "conflict"]})
        # A decision with a stated effect is applied as stated, after (and over) what the model read into decisions.
        stated = [{"id": item, "decision": e["id"], "status": e["effect"],
                   "text": e["new_text"] if e["effect"] == "changed" else ""}
                  for e in extra.get("effects", []) for item in e["items"]]
        consolidation = consolidation.model_copy(
            update={"updates": consolidation.updates + [Update.model_validate(u) for u in stated]})
        before = len(inventory.items)
        inventory = apply_consolidation(inventory, consolidation, {d["id"] for d in extra["decisions"]})
        joblog.note(f"consolidation merged {before - len(inventory.items)} item(s), "
                    f"applied {len(consolidation.updates)} update(s), {len(inventory.gaps)} question(s) in total")
    parts = extra["previous_parts"] + [{"source": p.source_id, "covers": p.label} for p, _ in fresh]
    suggestions = [x.model_dump() for x in consolidation.suggestions] if inventory.items else []
    return {"inventory": inventory.model_dump(), "parts": parts, "chunks": stored, "suggestions": suggestions,
            "again": not extra["new_input"],
            "minutes": [{"source": p.source_id, "covers": p.label, **m.model_dump()} for p, m in minutes],
            "minutes_read": sorted(set(extra["minutes_read"]) | {p.source_id for p, _ in minutes}),
            "decisions_applied": [d["id"] for d in extra["decisions"]],
            "_calls": calls, "_engine": ",".join(sorted({c["engine"] for c in calls})) or None}


def _apply_extract(session: Session, run: Run, execution: Execution, result: dict, team: Team) -> None:
    from controller.app.discovery import questions as q
    from controller.app.discovery.inventory import Inventory, check, render
    from controller.app.discovery.service import discovery_config, sources
    config = discovery_config(session, run)
    inventory = Inventory.model_validate(result["inventory"])
    names = {str(s.id): s.filename for s in sources(session, run.id)}
    decided = {q.decision_id(d) for d in q.decisions(session, run.id)}
    problems = check(inventory, set(names), decided)
    coarse = sum(1 for item in inventory.items
                 if all(c.location.startswith(("pages ", "lines ", "the whole")) for c in item.sources))
    title = run.title.removeprefix("Discovery: ")
    content = render(title, inventory, names)
    document = add_document(session, run.id, "inventory", content, author_kind="ai",
                            author=TeamRole.BUSINESS_ANALYST.value,
                            data={**inventory.model_dump(), "problems": problems, "coarse_citations": coarse,
                                  "sources": names, "again": result.get("again", False),
                                  **{k: result[k] for k in ("parts", "chunks", "minutes_read", "decisions_applied")}})
    document.commit_sha = commit_thread(session, run, "inventory", content,
                                        f"sdlc: requirement inventory v{document.version}")
    ids_by_name = {q._norm(n): i.id for i in inventory.items for n in [i.name, *i.aliases]}
    added, left_out, later = q.sync_questions(session, run, inventory.gaps, document.version, config, ids_by_name)
    suggested = q.apply_suggestions(session, run, result.get("suggestions", []))
    run_questions = q.questions(session, run.id)
    by_number = {q.number(run_questions, x): x for x in run_questions}
    proposed = 0
    for entry in result.get("minutes", []):
        for decision in entry["decisions"]:
            question = by_number.get(decision["question"])
            q.propose(session, run, decision["text"], question=question if question and question.status == q.OPEN else None,
                      items=decision["items"], source_id=uuid.UUID(entry["source"]), location=decision["location"],
                      config=config)
            proposed += 1
    write_discovery_documents(session, run, config, names)
    detail = f"{len(inventory.items)} item(s), {added} new question(s)"
    if later:
        detail += f", {later} detail question(s) deferred to the SRS/SDS"
    if left_out:
        detail += f" ({left_out} more left for the next round by discovery.limits.max_questions_per_round)"
    if proposed:
        detail += f", {proposed} decision(s) from minutes to accept"
    if suggested:
        detail += f", {suggested} suggested answer(s)"
    if problems:
        detail += f"; {len(problems)} problem(s) to review"
    transition_run(session, run, RunStatus.CLARIFYING, actor=TeamRole.BUSINESS_ANALYST.value,
                   reason=f"requirement inventory v{document.version}: {detail}")


def write_discovery_documents(session: Session, run: Run, config, names: dict[str, str]) -> None:
    """questions.md and decisions.md of the thread, as they stand now; a new version only when they changed."""
    from controller.app.discovery import questions as q
    title = run.title.removeprefix("Discovery: ")
    run_questions = q.questions(session, run.id)
    texts = {"questions": q.render_questions(run_questions, config, title),
             "decisions": q.render_decisions(q.decisions(session, run.id), run_questions, config, names, title)}
    for kind, content in texts.items():
        current = latest_document(session, run.id, kind)
        if current is None or current.content != content:
            document = add_document(session, run.id, kind, content, author_kind="ai", author=COORDINATOR)
            document.commit_sha = commit_thread(session, run, kind, content, f"sdlc: {kind} v{document.version}")
