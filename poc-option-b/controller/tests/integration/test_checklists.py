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
