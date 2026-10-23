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
