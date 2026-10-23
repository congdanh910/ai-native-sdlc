"""Several runs at once: one job per run, runs that change the same files wait for each other."""

import uuid

import pytest
from sqlalchemy import select

from controller.app import backlog, jobs, services
from controller.app.db import session_scope
from controller.app.models import Execution, Run
from controller.app.states import RunStatus
from controller.tests.integration.test_team_loop import (OPERATOR, TICKS, add_project, approve_spec, approve_tests,
                                                         drain, new_run, status)

pytestmark = pytest.mark.integration


def test_a_run_never_has_two_jobs_running_at_once(db, sample_repo):
    project_id = add_project(sample_repo)
    first, second = new_run(project_id), new_run(project_id)
    with session_scope() as session:
        claimed = jobs.claim(session, "worker-a")
        assert claimed.run_id == first
        jobs.enqueue(session, session.get(Run, first), "specify", "business_analyst", side_effects=False, key="extra")
    with session_scope() as session:
        other = jobs.claim(session, "worker-b")  # the first run is busy: the second run's job, though queued later
        assert other.run_id == second
    with session_scope() as session:
        assert jobs.claim(session, "worker-c") is None
        session.get(Execution, claimed.id).status = "succeeded"
    with session_scope() as session:
        assert jobs.claim(session, "worker-c").run_id == first


def _at_plan_gate(project_id) -> uuid.UUID:
    run_id = new_run(project_id)
    drain()
    approve_spec(run_id)
    drain()
    assert status(run_id) == RunStatus.WAITING_PLAN_APPROVAL
    return run_id


def _approve_plan(run_id, wait_for=()) -> None:
    from controller.app.documents import latest_plan
    with session_scope() as session:
        plan = latest_plan(session, run_id)
        services.approve_plan(session, services.get_run(session, run_id), plan.id, plan.sha256, actor=OPERATOR,
                              checked=TICKS, wait_for=[str(r) for r in wait_for])


def test_a_run_that_changes_the_same_files_can_wait_for_the_other_to_finish(db, sample_repo):
    project_id = add_project(sample_repo)
    first, second = _at_plan_gate(project_id), _at_plan_gate(project_id)
    with session_scope() as session:
        found = backlog.overlaps(session, session.get(Run, second))
    assert [o["run"] for o in found] == [str(first)] and found[0]["files"]
    assert not any(f.startswith(backlog.PLATFORM_PATHS) for f in found[0]["files"])
    with pytest.raises(services.DecisionError, match="does not change this plan's files"):
        _approve_plan(second, wait_for=[uuid.uuid4()])
    _approve_plan(second, wait_for=[first])
    approve_tests(second)
    with session_scope() as session:
        run = session.get(Run, second)
        assert run.status == RunStatus.READY and run.status_reason.startswith(f"waits for run {str(first)[:8]}")
        assert backlog.tick(session) == []  # the first run is still open
    with session_scope() as session:
        services.cancel(session, services.get_run(session, first), actor=OPERATOR, reason="not needed")
    with session_scope() as session:
        assert backlog.tick(session) == [f"run {str(second)[:8]} starts coding: the runs it waited for are finished"]
    assert status(second) == RunStatus.CODING
    drain()
    assert status(second) != RunStatus.READY


def test_the_tech_lead_can_let_a_waiting_run_code_now(db, sample_repo):
    project_id = add_project(sample_repo)
    first, second = _at_plan_gate(project_id), _at_plan_gate(project_id)
    _approve_plan(second, wait_for=[first])
    approve_tests(second)
    with session_scope() as session:
        services.stop_waiting(session, services.get_run(session, second), actor=OPERATOR, reason="small change")
    assert status(second) == RunStatus.CODING
    with session_scope() as session:
        with pytest.raises(services.DecisionError):
            services.stop_waiting(session, services.get_run(session, first), actor=OPERATOR, reason="x")


def _commit(repo, files: dict[str, str], message: str) -> None:
    from controller.tests.conftest import git
    for path, text in files.items():
        (repo / path).parent.mkdir(parents=True, exist_ok=True)
        (repo / path).write_text(text)
    git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "add", ".")
    git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "--quiet", "-m", message)


def test_main_merged_at_the_qc_gate_tests_the_story_again(db, sample_repo):
    from controller.app.coordinator import artifact
    from controller.tests.integration.test_acceptance import _at_the_qc_gate
    run_id = _at_the_qc_gate(sample_repo)
    with session_scope() as session:
        assert backlog.sync_with_main(session) == []  # main has not moved
    _commit(sample_repo, {"docs/other-story.md": "merged by another story\n"}, "another story")
    with session_scope() as session:
        assert [line.split(":")[0] for line in backlog.sync_with_main(session)] == [f"run {str(run_id)[:8]}"]
        assert backlog.sync_with_main(session) == []  # queued once
    drain()
    assert status(run_id) == RunStatus.WAITING_ACCEPTANCE_APPROVAL  # tested again on the merged code
    with session_scope() as session:
        import json
        sync = json.loads(artifact(session, run_id, "main_sync"))
        run = session.get(Run, run_id)
    assert sync["problems"] == [] and sync["validation"]["ok"]
    from controller.app import mainline
    from pathlib import Path
    assert mainline.is_up_to_date(Path(run.worktree_path), sync["main"])
    assert (Path(run.worktree_path) / "docs/other-story.md").is_file()


def test_a_code_conflict_with_main_goes_to_a_task_of_its_own(db, sample_repo):
    from pathlib import Path
    from controller.app.coordinator import current_tasks
    from controller.tests.integration.test_acceptance import _at_the_qc_gate
    run_id = _at_the_qc_gate(sample_repo)
    with session_scope() as session:
        worktree = Path(session.get(Run, run_id).worktree_path)
    _commit(worktree, {"src/shared.py": "VALUE = 'this story'\n"}, "feat(T1): this story")
    _commit(sample_repo, {"src/shared.py": "VALUE = 'another story'\n"}, "another story")
    with session_scope() as session:
        backlog.sync_with_main(session)
    from controller.tests.integration.test_team_loop import run_once
    from controller.app.team.mock import MockTeam
    assert run_once(MockTeam(), worker_id="test")  # the merge only; the conflict task is looked at below
    with session_scope() as session:
        run = session.get(Run, run_id)
        assert run.status == RunStatus.CODING and "code conflict in src/shared.py" in run.status_reason
        task = current_tasks(session, run)[-1]
        assert task.payload["title"] == "Resolve the conflicts with the default branch"
        assert task.payload["conflicts"]["files"] == ["src/shared.py"] and "src/shared.py" in task.payload["modify"]
    drain()  # the mock Developer changes nothing: the markers stay and the task stops for a person
    with session_scope() as session:
        run = session.get(Run, run_id)
        assert run.status == RunStatus.NEEDS_HUMAN and "validation failed" in run.status_reason
    assert "<<<<<<<" in (worktree / "src/shared.py").read_text()
