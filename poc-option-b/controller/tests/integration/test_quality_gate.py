"""The Quality gate: the QC reviews the test cases after the plan gate and before any code."""

import uuid

from fastapi.testclient import TestClient
import pytest

from controller.app.main import app
from controller.app.team.mock import MockTeam
from controller.tests.integration.test_srs_flow import post
from controller.tests.integration.test_team_loop import TICKS, drain

pytestmark = pytest.mark.integration
client = TestClient(app, headers={"X-SDLC-Client": "web"})


def at_quality_gate(sample_repo) -> str:
    post("/api/projects", {"key": "task-board", "repo_url": str(sample_repo)}, status=201)
    intent = {"title": "Due dates", "problem": "No deadlines.", "outcome": "- Optional due date\n- Overdue tasks show red"}
    run = post("/api/runs", {"project": "task-board", "intent": intent, "idempotency_key": str(uuid.uuid4())},
               status=201)["id"]
    drain()
    spec = [d for d in client.get(f"/api/runs/{run}").json()["documents"] if d["kind"] == "spec"][-1]
    post(f"/api/runs/{run}/approve-spec", {"document_id": spec["id"], "sha256": spec["sha256"], "checked": TICKS})
    drain()
    plan = client.get(f"/api/runs/{run}").json()["plans"][-1]
    post(f"/api/runs/{run}/approve-plan", {"plan_id": plan["id"], "sha256": plan["sha256"], "checked": TICKS})
    return run


def latest(run: str, kind: str) -> dict:
    return [d for d in client.get(f"/api/runs/{run}").json()["documents"] if d["kind"] == kind][-1]


def test_no_task_starts_before_the_qc_approves(db, sample_repo):
    run = at_quality_gate(sample_repo)
    detail = client.get(f"/api/runs/{run}").json()
    assert detail["status"] == "WAITING_TEST_APPROVAL" and detail["tasks"] == []
    tests = latest(run, "tests")
    assert "### Coverage Matrix" in tests["content"] and "| AC-01 — Optional due date | API | TC-01 |" in tests["content"]
    stale = client.post(f"/api/runs/{run}/approve-tests", json={"document_id": tests["id"], "sha256": "0" * 64})
    assert stale.status_code == 409
    detail = post(f"/api/runs/{run}/approve-tests", {"document_id": tests["id"], "sha256": tests["sha256"], "checked": TICKS})
    assert detail["tasks"] and [a["role"] for a in detail["approvals"]] == ["product_owner", "tech_lead", "qc"]
    before = [d for d in detail["documents"] if d["kind"] == "analysis"][-1]
    assert before["content"].startswith("# Analysis before: Due dates") and "**0** Critical" in before["content"]
    drain()
    detail = client.get(f"/api/runs/{run}").json()
    after = [d for d in detail["documents"] if d["kind"] == "analysis"][-1]
    assert after["content"].startswith("# Analysis after: Due dates")
    assert any(c["id"] == "analysis" for c in detail["checklist"]["controller"])


def test_qc_revision_keeps_the_approved_plan(db, sample_repo):
    run = at_quality_gate(sample_repo)
    plan = client.get(f"/api/runs/{run}").json()["plans"][-1]
    cases = plan["test_plan"]["cases"]
    wrong = {"cases": [{**cases[0], "task_id": "T9"}]}
    response = client.post(f"/api/runs/{run}/revise-tests", json={"test_plan": wrong})
    assert response.status_code == 409 and "T9" in response.json()["detail"]
    edited = {"cases": [{**cases[0], "severity": "Critical", "name": "Due date is optional"}, *cases[1:]]}
    detail = post(f"/api/runs/{run}/revise-tests", {"test_plan": edited, "reason": "make it critical"})
    assert detail["status"] == "WAITING_TEST_APPROVAL"
    plans = detail["plans"]
    assert len(plans) == 2 and plans[1]["plan"] == plans[0]["plan"] and plans[1]["author_kind"] == "human"
    tests = latest(run, "tests")
    assert tests["version"] == 2 and "### TC-01: Due date is optional" in tests["content"]
    assert "- **Severity**: Critical" in tests["content"]
    post(f"/api/runs/{run}/approve-tests", {"document_id": tests["id"], "sha256": tests["sha256"], "checked": TICKS})


def test_qc_rejection_sends_the_reason_to_the_qa_analyst(db, sample_repo):
    run = at_quality_gate(sample_repo)
    post(f"/api/runs/{run}/reject-tests", {"reason": "add a case for an empty date", "next": "redo"})
    seen = []

    class Recording(MockTeam):
        def design_tests(self, context, plan):
            seen.append(context.feedback)
            return super().design_tests(context, plan)

    drain(Recording())
    assert seen == [["QC asked for changes: add a case for an empty date"]]
    detail = client.get(f"/api/runs/{run}").json()
    assert detail["status"] == "WAITING_TEST_APPROVAL" and latest(run, "tests")["version"] == 2


def test_qc_can_send_the_plan_back_to_the_tech_lead(db, sample_repo):
    run = at_quality_gate(sample_repo)
    post(f"/api/runs/{run}/reject-tests", {"reason": "the plan has no task for the overdue colour", "next": "replan"})
    drain()
    detail = client.get(f"/api/runs/{run}").json()
    assert detail["status"] == "WAITING_PLAN_APPROVAL" and len(detail["plans"]) == 2
