"""The whole loop with the mock team against a real PostgreSQL."""

from datetime import timedelta
import uuid

import pytest
from sqlalchemy import select

from controller.app import coordinator, jobs, services
from controller.app.checklists import HUMAN_ITEMS
from controller.app.db import session_scope
from controller.app.documents import latest_document, latest_plan
from controller.app.models import Execution, Plan, Project, PullRequest, Run, Task
from controller.app.projects import registry
from controller.app.projects.repo import Mirror
from controller.app.schemas import Intent
from controller.app.state_machine import InvalidTransition, StaleVersion, transition_run
from controller.app.states import RunStatus
from controller.app.team.base import CapacityBusy, StepResult
from controller.app.team.mock import MockTeam
from controller.app.worker import run_once
from controller.tests.conftest import git

TICKS = sorted({item.id for items in HUMAN_ITEMS.values() for item in items})  # every review item ticked

pytestmark = pytest.mark.integration
OPERATOR = "tester"
INTENT = Intent(title="Due dates", problem="Tasks have no deadline.",
                outcome="- Each task can have an optional due date\n- Overdue tasks are highlighted")


def drain(team=None, limit: int = 50) -> None:
    team = team or MockTeam()
    for _ in range(limit):
        if not run_once(team, worker_id="test"):
            return
    raise AssertionError("queue did not drain")


def add_project(repo) -> uuid.UUID:
    with session_scope() as session:
        return registry.add_project(session, "task-board", str(repo), actor=OPERATOR).id


def new_run(project_id, intent: Intent = INTENT, key: str | None = None) -> uuid.UUID:
    with session_scope() as session:
        project = session.get(Project, project_id)
        return services.create_run(session, project, intent, actor=OPERATOR,
                                   idempotency_key=key or str(uuid.uuid4())).id


def status(run_id) -> str:
    with session_scope() as session:
        return session.get(Run, run_id).status


def approve_spec(run_id) -> None:
    with session_scope() as session:
        run = services.get_run(session, run_id)
        spec = latest_document(session, run_id, "spec")
        services.approve_spec(session, run, spec.id, spec.sha256, actor=OPERATOR, checked=TICKS)


def approve_plan(run_id, *, tests: bool = True) -> None:
    """The Tech Lead approves the plan and, unless tests=False, the QC approves its test cases."""
    with session_scope() as session:
        run = services.get_run(session, run_id)
        plan = latest_plan(session, run_id)
        services.approve_plan(session, run, plan.id, plan.sha256, actor=OPERATOR, checked=TICKS)
    if tests:
        approve_tests(run_id)


def approve_tests(run_id) -> None:
    with session_scope() as session:
        run = services.get_run(session, run_id)
        document = latest_document(session, run_id, "tests")
        services.approve_tests(session, run, document.id, document.sha256, actor=OPERATOR, checked=TICKS)


def set_stages(repo, **stages) -> None:
    config = repo / ".sdlc" / "project.yaml"
    text = config.read_text()
    for stage, mode in stages.items():
        text = text.replace(f"  {stage}: gated", f"  {stage}: {mode}").replace(f"  {stage}: auto", f"  {stage}: {mode}")
    config.write_text(text)
    git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "--quiet", "-am", "stages")


def test_onboarding_reports_plan_only(db, sample_repo):
    project_id = add_project(sample_repo)
    with session_scope() as session:
        project = session.get(Project, project_id)
        names = {item["name"]: item["status"] for item in project.readiness_report["items"]}
    assert project.readiness == "plan_only"
    assert names["AGENTS.md"] == "ok" and names["CLAUDE.md"] == "ok" and names[".sdlc/project.yaml"] == "ok"
    assert names["validation commands"] == "pending"


def test_project_without_config_is_not_ready(db, sample_repo):
    git(sample_repo, "rm", "--quiet", ".sdlc/project.yaml")
    git(sample_repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "--quiet", "-m", "drop config")
    project_id = add_project(sample_repo)
    with session_scope() as session:
        project = session.get(Project, project_id)
        assert project.readiness == "not_ready"
        with pytest.raises(services.DecisionError, match="not ready"):
            services.create_run(session, project, INTENT, actor=OPERATOR, idempotency_key="k")


def test_full_loop_commits_the_artifact_thread(db, sample_repo):
    run_id = new_run(add_project(sample_repo))
    drain()
    assert status(run_id) == RunStatus.WAITING_SPEC_APPROVAL
    approve_spec(run_id)
    drain()
    assert status(run_id) == RunStatus.WAITING_PLAN_APPROVAL
    with session_scope() as session:
        plan = latest_plan(session, run_id)
        assert [t["modify"] for t in plan.payload["tasks"]][0][0].startswith("src/")
        assert len(plan.test_plan["cases"]) == 2
        assert plan.grounding_report == {"status": "pass", "findings": [], "warnings": []}
    approve_plan(run_id)
    drain()
    assert status(run_id) == RunStatus.WAITING_PR_APPROVAL

    with session_scope() as session:
        run = session.get(Run, run_id)
        tasks = session.scalars(select(Task).where(Task.run_id == run_id)).all()
        assert {t.status for t in tasks} == {"PASSED"}
        pull = session.scalar(select(PullRequest).where(PullRequest.run_id == run_id))
        assert pull.simulated
        mirror = Mirror(registry.mirror_for(session.get(Project, run.project_id)).path)
        thread = f".sdlc/runs/{str(run_id)[:8]}"
        head = mirror.resolve(run.branch)
        assert head == pull.head_sha
        for kind in ("intent", "spec", "plan", "review", "report"):
            assert mirror.read_file(head, f"{thread}/{kind}.md"), kind
        spec = latest_document(session, run_id, "spec")
        assert mirror.read_file(head, f"{thread}/spec.md") == spec.content
        services.pr_decision(session, services.get_run(session, run_id), actor=OPERATOR, decision="approve", checked=TICKS,
                             reason=None)
    assert status(run_id) == RunStatus.COMPLETED


def test_stale_spec_version_is_rejected(db, sample_repo):
    run_id = new_run(add_project(sample_repo))
    drain()
    with session_scope() as session:
        run = services.get_run(session, run_id)
        old = latest_document(session, run_id, "spec")
        edited = old.content.replace("- AC-2:", "- AC-2: (edited)")
        services.revise_spec(session, run, edited, actor=OPERATOR, reason="clarify")
    with session_scope() as session:
        run = services.get_run(session, run_id)
        with pytest.raises(services.DecisionError, match="latest spec"):
            services.approve_spec(session, run, old.id, old.sha256, actor=OPERATOR, checked=TICKS)
    approve_spec(run_id)
    with session_scope() as session:
        assert session.get(Run, run_id).human_corrected


def test_revised_spec_must_keep_criteria(db, sample_repo):
    run_id = new_run(add_project(sample_repo))
    drain()
    with session_scope() as session:
        with pytest.raises(services.DecisionError, match="AC-01"):
            services.revise_spec(session, services.get_run(session, run_id), "# no criteria", actor=OPERATOR,
                                 reason=None)


def test_blocking_question_waits_for_the_product_owner(db, sample_repo):
    intent = INTENT.model_copy(update={"open_questions": "- [blocking] Which date format?\n- Colours later?"})
    run_id = new_run(add_project(sample_repo), intent)
    drain()
    assert status(run_id) == RunStatus.NEEDS_CLARIFICATION
    with session_scope() as session:
        run = services.get_run(session, run_id)
        from controller.app.models import Clarification
        question = session.scalar(select(Clarification).where(Clarification.run_id == run_id))
        services.answer_questions(session, run, {str(question.id): "YYYY-MM-DD"}, actor=OPERATOR)
    drain()
    assert status(run_id) == RunStatus.WAITING_SPEC_APPROVAL
    with session_scope() as session:
        spec = latest_document(session, run_id, "spec")
    assert "YYYY-MM-DD" in spec.content and "Colours later?" in spec.content


def test_auto_spec_and_manual_coding(db, sample_repo):
    set_stages(sample_repo, spec="auto", coding="manual")
    run_id = new_run(add_project(sample_repo))
    drain()
    assert status(run_id) == RunStatus.WAITING_PLAN_APPROVAL
    approve_plan(run_id)
    drain()
    with session_scope() as session:
        run = session.get(Run, run_id)
        assert (run.status, run.awaiting_start) == (RunStatus.CODING, "coding")
        services.start_stage(session, services.get_run(session, run_id), "coding", actor=OPERATOR,
                             acting_role="tech_lead")
    drain()
    assert status(run_id) == RunStatus.WAITING_PR_APPROVAL


def test_transitions_are_checked(db, sample_repo):
    run_id = new_run(add_project(sample_repo))
    with session_scope() as session:
        run = services.get_run(session, run_id)
        with pytest.raises(InvalidTransition):
            transition_run(session, run, RunStatus.COMPLETED, actor=OPERATOR)
        with pytest.raises(StaleVersion):
            transition_run(session, run, RunStatus.SPECIFYING, actor=OPERATOR, expected_version=run.version - 1)


def test_create_run_and_enqueue_are_idempotent(db, sample_repo):
    project_id = add_project(sample_repo)
    first, second = new_run(project_id, key="same"), new_run(project_id, key="same")
    assert first == second
    with session_scope() as session:
        run = session.get(Run, first)
        coordinator.queue(session, run, "preflight", key="v2")
        coordinator.queue(session, run, "preflight", key="v2")
        assert len(session.scalars(select(Execution).where(Execution.run_id == first)).all()) == 1


def test_expired_coding_lease_is_never_replayed(db, sample_repo):
    run_id = new_run(add_project(sample_repo))
    drain()
    approve_spec(run_id)
    drain()
    approve_plan(run_id)
    with session_scope() as session:
        execution = jobs.claim(session, "crashed-worker")
        assert execution.kind == "code_task"
        coordinator.on_claim(session, execution)
        execution.lease_until = jobs.now() - timedelta(seconds=1)
    with session_scope() as session:
        jobs.reconcile_expired(session)
    with session_scope() as session:
        run = session.get(Run, run_id)
        execution = session.scalar(select(Execution).where(Execution.worker_id == "crashed-worker"))
        task = session.get(Task, execution.task_id)
        assert (run.status, execution.status, task.status) == ("NEEDS_HUMAN", "unknown", "UNKNOWN")
        assert services.needs_classification(run)
        services.resume(session, services.get_run(session, run_id), actor=OPERATOR, decision="resume",
                        reason="worktree inspected; nothing was written")
    drain()
    assert status(run_id) == RunStatus.WAITING_PR_APPROVAL


def test_expired_reasoning_lease_is_requeued(db, sample_repo):
    run_id = new_run(add_project(sample_repo))
    with session_scope() as session:
        execution = jobs.claim(session, "crashed-worker")
        execution.lease_until = jobs.now() - timedelta(seconds=1)
    with session_scope() as session:
        jobs.reconcile_expired(session)
    drain()
    assert status(run_id) == RunStatus.WAITING_SPEC_APPROVAL


class FailingValidation(MockTeam):
    def validate(self, context, task, attempt=1):
        return StepResult(False, "unit-tests failed")


class DisputingDeveloper(MockTeam):
    def implement(self, context, task, attempt, tests=None, feedback=""):
        return StepResult(False, "test TC-T1-1 asserts the wrong date format", dispute=True)


def _to_coding(sample_repo) -> uuid.UUID:
    run_id = new_run(add_project(sample_repo))
    drain()
    approve_spec(run_id)
    drain()
    approve_plan(run_id)
    return run_id


def test_validation_failure_after_remediation_needs_a_human(db, sample_repo):
    run_id = _to_coding(sample_repo)
    drain(FailingValidation())
    with session_scope() as session:
        run = session.get(Run, run_id)
        task = session.scalar(select(Task).where(Task.run_id == run_id, Task.external_task_id == "T1"))
        assert (run.status, task.status) == ("NEEDS_HUMAN", "FAILED")
        assert "after 2 remediations" in run.status_reason
        services.classify(session, run, actor=OPERATOR, category="WEAK_TESTS", stage="validation",
                          role="developer", note="mock")
        assert not services.needs_classification(run)


def test_test_dispute_goes_to_a_human(db, sample_repo):
    run_id = _to_coding(sample_repo)
    drain(DisputingDeveloper())
    with session_scope() as session:
        task = session.scalar(select(Task).where(Task.run_id == run_id, Task.external_task_id == "T1"))
        assert (status(run_id), task.status) == ("NEEDS_HUMAN", "DISPUTED")


def test_cancelled_run_discards_queued_work(db, sample_repo):
    run_id = new_run(add_project(sample_repo))
    with session_scope() as session:
        services.cancel(session, services.get_run(session, run_id), actor=OPERATOR, reason="not needed")
    drain()
    with session_scope() as session:
        execution = session.scalar(select(Execution).where(Execution.run_id == run_id))
    assert (status(run_id), execution.status) == ("CANCELLED", "discarded")


def test_requested_changes_go_back_through_coding(db, sample_repo):
    run_id = _to_coding(sample_repo)
    drain()
    with session_scope() as session:
        services.pr_decision(session, services.get_run(session, run_id), actor=OPERATOR,
                             decision="request_changes", reason="rename the field")
    drain()
    with session_scope() as session:
        run = session.get(Run, run_id)
        attempts = {t.attempt_count for t in session.scalars(select(Task).where(Task.run_id == run_id))}
    assert run.status == RunStatus.WAITING_PR_APPROVAL and attempts == {2} and run.human_corrected


def test_project_can_use_another_branch(db, sample_repo):
    git(sample_repo, "switch", "--quiet", "-c", "onboarding")
    git(sample_repo, "switch", "--quiet", "main")
    with session_scope() as session:
        project = registry.add_project(session, "task-board", str(sample_repo), actor=OPERATOR, branch="onboarding")
        assert project.default_branch == "onboarding" and project.readiness == "plan_only"
        with pytest.raises(registry.ProjectError, match="cannot use repository"):
            registry.add_project(session, "other-board", str(sample_repo), actor=OPERATOR, branch="missing")


class InventingPlanner(MockTeam):
    """Plans against a file that does not exist, like the pilot's renderBoard."""
    def __init__(self):
        self.feedback_seen = []

    def plan(self, context):
        self.feedback_seen.append(list(context.feedback))
        plan = super().plan(context)
        first = plan.tasks[0].model_copy(update={"modify": ["src/board.js"]})
        return plan.model_copy(update={"tasks": [first, *plan.tasks[1:]]})


def test_ungrounded_plan_is_regenerated_once_then_shown_with_findings(db, sample_repo):
    team = InventingPlanner()
    run_id = new_run(add_project(sample_repo))
    drain(team)
    approve_spec(run_id)
    drain(team)
    assert status(run_id) == RunStatus.WAITING_PLAN_APPROVAL
    assert team.feedback_seen[0] == [] and "src/board.js" in team.feedback_seen[1][0]
    with session_scope() as session:
        plans = session.scalars(select(Plan).where(Plan.run_id == run_id).order_by(Plan.version)).all()
        assert [p.grounding_report["status"] for p in plans] == ["fail", "fail"]
        assert plans[1].regenerated_from == plans[0].id
        assert "grounding failed with 1 finding" in session.get(Run, run_id).status_reason


def test_human_revision_is_grounded_too(db, sample_repo):
    run_id = new_run(add_project(sample_repo))
    drain()
    approve_spec(run_id)
    drain()
    with session_scope() as session:
        run = services.get_run(session, run_id)
        plan = latest_plan(session, run_id)
        payload = dict(plan.payload)
        payload["tasks"] = [{**payload["tasks"][0], "modify": ["src/missing.js"]}, *payload["tasks"][1:]]
        services.revise_plan(session, run, payload, plan.test_plan, actor=OPERATOR, reason="try")
    with session_scope() as session:
        assert latest_plan(session, run_id).grounding_report["status"] == "fail"


class BusyGx10(MockTeam):
    def specify(self, context):
        raise CapacityBusy("GX10 is busy: GPU utilization 95% is above 20%")


def test_busy_gx10_postpones_without_using_an_attempt(db, sample_repo):
    run_id = new_run(add_project(sample_repo))
    drain(BusyGx10())
    with session_scope() as session:
        execution = session.scalar(select(Execution).where(Execution.run_id == run_id, Execution.kind == "specify"))
        assert (execution.status, execution.attempts) == ("queued", 0)
        assert execution.available_at > jobs.now() and "GPU" in execution.error
    assert status(run_id) == RunStatus.SPECIFYING


def test_rejection_reasons_reach_the_business_analyst_and_the_tech_lead(db, sample_repo):
    from controller.app.coordinator import load
    from controller.app.models import Execution
    seen: list[tuple[str, list[str], str | None]] = []

    class Recording(MockTeam):
        def specify(self, context):
            seen.append(("spec", list(context.feedback), context.spec_md))
            return super().specify(context)

        def plan(self, context):
            seen.append(("plan", list(context.feedback), None))
            return super().plan(context)
    team = Recording()
    run_id = new_run(add_project(sample_repo))
    drain(team)
    with session_scope() as session:
        run = services.get_run(session, run_id)
        first = latest_document(session, run_id, "spec").content
        services.reject_spec(session, run, actor=OPERATOR, reason="Due dates must use the team's time zone",
                             next_step="redo")
    drain(team)
    assert seen[-1] == ("spec", ["Reviewer asked for changes: Due dates must use the team's time zone"], first)
    approve_spec(run_id)
    drain(team)
    with session_scope() as session:
        services.reject_plan(session, services.get_run(session, run_id), actor=OPERATOR,
                             reason="Keep the date rules in src/tasks.js", next_step="redo")
    drain(team)
    assert seen[-1][0] == "plan" and "Tech Lead rejected the plan: Keep the date rules in src/tasks.js" in seen[-1][1]
