"""Review checklists at the gates: controller checks block, human items must be ticked, both are recorded."""

import uuid

from fastapi.testclient import TestClient
import pytest

from controller.app import checklists
from controller.app.main import app
from controller.app.schemas import Criterion
from controller.tests.integration.test_srs_flow import post
from controller.tests.integration.test_team_loop import TICKS, drain

pytestmark = pytest.mark.integration
client = TestClient(app, headers={"X-SDLC-Client": "web"})


def at_spec_gate(sample_repo) -> tuple[str, dict]:
    post("/api/projects", {"key": "task-board", "repo_url": str(sample_repo)}, status=201)
    intent = {"title": "Due dates", "problem": "No deadlines.", "outcome": "- Optional due date"}
    run = post("/api/runs", {"project": "task-board", "intent": intent, "idempotency_key": str(uuid.uuid4())},
               status=201)["id"]
    drain()
    return run, client.get(f"/api/runs/{run}").json()


def test_the_gate_shows_its_checklist_and_refuses_unticked_items(db, sample_repo):
    run, detail = at_spec_gate(sample_repo)
    checklist = detail["checklist"]
    assert checklist["gate"] == "spec"
    assert all(c["ok"] for c in checklist["controller"]), checklist["controller"]
    assert [a["id"] for a in checklist["ai"]] == [i.id for i in checklists.AI_ITEMS["spec"]]
    assert [h["id"] for h in checklist["human"]] == ["full-scenario", "clear", "deferrals"]
    spec = [d for d in detail["documents"] if d["kind"] == "spec"][-1]
    response = client.post(f"/api/runs/{run}/approve-spec",
                           json={"document_id": spec["id"], "sha256": spec["sha256"], "checked": ["clear"]})
    assert response.status_code == 409 and "The ACs cover the whole scenario" in response.json()["detail"]
    detail = post(f"/api/runs/{run}/approve-spec", {"document_id": spec["id"], "sha256": spec["sha256"],
                                                     "checked": TICKS})
    recorded = detail["approvals"][-1]["checklist"]
    assert recorded["gate"] == "spec" and all(h["checked"] for h in recorded["human"])
    assert {c["id"] for c in recorded["controller"]} >= {"ids", "gwt", "banned-words"}


def test_a_failing_controller_check_blocks_the_approval(db, sample_repo):
    run, detail = at_spec_gate(sample_repo)
    spec = [d for d in detail["documents"] if d["kind"] == "spec"][-1]
    vague = spec["content"].replace("**Then** ", "**Then** the page loads fast and ", 1)
    post(f"/api/runs/{run}/revise-spec", {"content": vague, "reason": "wording"})
    detail = client.get(f"/api/runs/{run}").json()
    assert not next(c for c in detail["checklist"]["controller"] if c["id"] == "banned-words")["ok"]
    spec = [d for d in detail["documents"] if d["kind"] == "spec"][-1]
    response = client.post(f"/api/runs/{run}/approve-spec",
                           json={"document_id": spec["id"], "sha256": spec["sha256"], "checked": TICKS})
    assert response.status_code == 409 and "AC-01 uses 'fast'" in response.json()["detail"]


def test_requirement_items_follow_the_self_check():
    statuses = checklists.requirement_statuses([{"id": "testable", "status": "pass", "note": ""},
                                                {"id": "edge-cases", "status": "deferred", "note": "no concurrency case"}])
    assert statuses["Requirements are testable and unambiguous"] == ("pass", "")
    assert statuses["Edge cases are identified"] == ("deferred", "accepted at the spec gate: no concurrency case")
    assert statuses["Scope is clearly bounded"] == ("pass", "")
    assert statuses["No implementation details leak into specification"][0] == "deferred"


def test_controller_checks_name_the_problem():
    criteria = [Criterion(id="AC-01", text="Login", given="a user", when="they POST /api/login", then="ok"),
                Criterion(id="AC-03", text="Login", given="", when="x", then="y")]
    results = {r["id"]: r for r in checklists.check_criteria(criteria)}
    assert results["ids"]["detail"] == "AC ids are AC-01, AC-03; expected AC-01, AC-02"
    assert results["gwt"]["detail"] == "AC-03 lack a clause"
    assert results["names"]["detail"] == "repeated: login"
    assert results["no-paths"]["detail"] == "AC-01 name code"
    release = {r["id"]: r for r in checklists.check_release({"T1": "PASSED"}, ['const password = "hunter22";'], [])}
    assert not release["secrets"]["ok"] and release["tasks"]["ok"]


def _gate_check(run_id, item: str) -> dict:
    return next(c for c in client.get(f"/api/runs/{run_id}").json()["checklist"]["controller"] if c["id"] == item)


def test_a_blocking_finding_says_what_to_do_and_a_forgotten_criterion_is_added_without_a_model(db, sample_repo):
    from controller.app import services
    from controller.app.db import session_scope
    from controller.app.documents import latest_plan
    from controller.tests.integration.test_team_loop import (OPERATOR, add_project, approve_plan, approve_spec,
                                                             new_run, status)
    run_id = new_run(add_project(sample_repo))
    drain()
    approve_spec(run_id)
    drain()
    approve_plan(run_id, tests=False)
    with session_scope() as session:
        tests = latest_plan(session, run_id).test_plan
    moved = {**tests, "cases": [{**c, "task_id": "T1"} if c["criterion"] == "AC-02" else c for c in tests["cases"]]}
    with session_scope() as session:  # on T1, in the tests' folder: the test sits on the wrong task
        services.revise_tests(session, services.get_run(session, run_id), moved, actor=OPERATOR, reason="probe")
    fixes = _gate_check(run_id, "analysis")["fixes"]
    assert fixes[0]["action"] == "redesign_tests" and "Move TC-T2-1 to T2" in fixes[0]["reason"] and "amend" not in fixes[0]
    beside = {**moved, "cases": [{**c, "target_file": "src/app.test.js"} if c["criterion"] == "AC-02" else c
                                 for c in moved["cases"]]}
    with session_scope() as session:  # next to T1's code: T1 builds what it proves, the plan forgot AC-02 on T1
        services.revise_tests(session, services.get_run(session, run_id), beside, actor=OPERATOR, reason="probe")
    check = _gate_check(run_id, "analysis")
    fix = check["fixes"][0]
    assert not check["ok"] and fix["action"] == "replan" and fix["amend"] == {"task": "T1", "criterion": "AC-02"}
    assert fix["reason"].startswith("Add AC-02 to T1's acceptance criteria")
    post(f"/api/runs/{run_id}/amend-plan", {**fix["amend"], "reason": fix["reason"]})
    assert _gate_check(run_id, "analysis")["ok"] and status(run_id) == "WAITING_TEST_APPROVAL"
    with session_scope() as session:
        plan = latest_plan(session, run_id)
        assert plan.payload["tasks"][0]["acceptance_criteria"] == ["AC-01", "AC-02"]
        assert plan.test_plan == beside
    assert client.post(f"/api/runs/{run_id}/amend-plan", json=fix["amend"]).status_code == 409  # already there


def test_a_failing_check_without_findings_of_its_own_gets_the_gate_s_standard_next_step():
    from controller.app.guidance import check_fixes
    assert check_fixes("tests", "coverage", "no test: AC-03")[0] == {
        "action": "redesign_tests", "why": "Every criterion needs a test case.", "reason": "Add a test case for each criterion that has none (no test: AC-03)."}
    assert check_fixes("plan", "unknown-item", "x") == []


def test_criteria_the_plan_forgot_on_the_tasks_that_build_them_are_added_by_the_controller(db, sample_repo):
    from controller.app import services
    from controller.app.db import session_scope
    from controller.app.documents import latest_plan
    from controller.app.schemas import TestPlan
    from controller.app.team.mock import MockTeam
    from controller.tests.integration.test_team_loop import (OPERATOR, add_project, approve_plan, approve_spec,
                                                             new_run, status)

    class ForgetfulPlan(MockTeam):
        """Redesigned, the QA Analyst proves AC-02 with a test next to T1's code; the plan has AC-02 on T2 only."""
        def design_tests(self, context, plan):
            tests = super().design_tests(context, plan)
            return TestPlan.model_validate({**tests.model_dump(), "cases": [
                {**c.model_dump(), "task_id": "T1", "target_file": "src/app.test.js"} if c.criterion == "AC-02"
                else c.model_dump() for c in tests.cases]})

    run_id = new_run(add_project(sample_repo))
    drain()
    approve_spec(run_id)
    drain()
    approve_plan(run_id, tests=False)
    with session_scope() as session:
        services.reject_tests(session, services.get_run(session, run_id), actor=OPERATOR,
                              reason="cover AC-02 where it is built", next_step="redo")
    drain(ForgetfulPlan())
    assert status(run_id) == "WAITING_TEST_APPROVAL" and _gate_check(run_id, "analysis")["ok"]
    with session_scope() as session:
        plan = latest_plan(session, run_id)
        assert plan.payload["tasks"][0]["acceptance_criteria"] == ["AC-01", "AC-02"] and plan.author_kind == "ai"
