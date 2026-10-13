"""The web control room's API calls, end to end with the mock team."""

import uuid

from fastapi.testclient import TestClient
import pytest

from controller.app.main import app
from controller.tests.integration.test_team_loop import drain

pytestmark = pytest.mark.integration
client = TestClient(app, headers={"X-SDLC-Client": "web"})


def post(path: str, body: dict | None = None, status: int = 200) -> dict:
    response = client.post(path, json=body or {})
    assert response.status_code == status, response.text
    return response.json()


def test_operator_walks_the_loop_in_the_web_api(db, sample_repo):
    project = post("/api/projects", {"key": "task-board", "repo_url": str(sample_repo)}, status=201)
    assert project["readiness"] == "plan_only"

    draft = post("/api/intents/draft", {"title": "Due dates", "text": "Tasks need deadlines. Show overdue tasks."})
    run = post("/api/runs", {"project": "task-board", "intent": draft, "idempotency_key": str(uuid.uuid4())},
               status=201)
    drain()

    inbox = client.get("/api/inbox").json()
    assert [(i["role"], i["action"]) for i in inbox] == [("product_owner", "waiting_spec_approval")]
    detail = client.get(f"/api/runs/{run['id']}").json()
    spec = [d for d in detail["documents"] if d["kind"] == "spec"][-1]

    stale = client.post(f"/api/runs/{run['id']}/approve-spec", json={"document_id": spec["id"], "sha256": "0" * 64})
    assert stale.status_code == 409
    post(f"/api/runs/{run['id']}/approve-spec", {"document_id": spec["id"], "sha256": spec["sha256"]})
    drain()

    detail = client.get(f"/api/runs/{run['id']}").json()
    assert detail["waiting"] == {"role": "tech_lead", "action": "waiting_plan_approval"}
    plan = detail["plans"][-1]
    revised = post(f"/api/runs/{run['id']}/revise-plan",
                   {"plan": {**plan["plan"], "summary": "Edited by the Tech Lead"}, "test_plan": plan["test_plan"],
                    "reason": "clearer summary"})
    plan = revised["plans"][-1]
    assert plan["version"] == 2 and plan["author_kind"] == "human"
    post(f"/api/runs/{run['id']}/approve-plan", {"plan_id": plan["id"], "sha256": plan["sha256"]})
    drain()

    detail = client.get(f"/api/runs/{run['id']}").json()
    assert detail["status"] == "WAITING_PR_APPROVAL" and detail["pull_request"]["simulated"]
    detail = post(f"/api/runs/{run['id']}/pr-decision", {"decision": "approve"})
    assert detail["status"] == "COMPLETED"
    assert detail["waiting"] == {"role": "tech_lead", "action": "classify"}  # the plan was corrected
    post(f"/api/runs/{run['id']}/classify-failure",
         {"category": "AMBIGUOUS_SPEC", "stage": "planning", "role": "tech_lead_ai", "note": "summary unclear"})
    assert client.get("/api/inbox").json() == []

    events = client.get(f"/api/runs/{run['id']}/events").json()
    assert {"spec.approve", "plan.revise", "plan.approve", "pr.approve", "failure.classified"} <= {e["type"] for e in events}


def test_bad_plan_revision_is_explained(db, sample_repo):
    post("/api/projects", {"key": "task-board", "repo_url": str(sample_repo)}, status=201)
    draft = {"title": "Due dates", "problem": "No deadlines.", "outcome": "- Optional due date"}
    run = post("/api/runs", {"project": "task-board", "intent": draft, "idempotency_key": str(uuid.uuid4())},
               status=201)
    drain()
    detail = client.get(f"/api/runs/{run['id']}").json()
    spec = [d for d in detail["documents"] if d["kind"] == "spec"][-1]
    post(f"/api/runs/{run['id']}/approve-spec", {"document_id": spec["id"], "sha256": spec["sha256"]})
    drain()
    plan = client.get(f"/api/runs/{run['id']}").json()["plans"][-1]
    cyclic = {**plan["plan"], "tasks": [{**plan["plan"]["tasks"][0], "depends_on": ["T1"]}]}
    response = client.post(f"/api/runs/{run['id']}/revise-plan", json={"plan": cyclic, "test_plan": plan["test_plan"]})
    assert response.status_code == 409 and "cycle" in response.json()["detail"]


def test_unknown_project_key_is_rejected(db):
    response = client.post("/api/projects", json={"key": "Bad Key", "repo_url": "/nowhere"})
    assert response.status_code == 400
