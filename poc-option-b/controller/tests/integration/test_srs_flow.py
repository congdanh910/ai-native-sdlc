"""The project SRS run: brief, Business Analyst, SRS gate, SRS.md in a draft PR."""

import subprocess
import uuid

from fastapi.testclient import TestClient
import pytest

from controller.app.db import session_scope
from controller.app.main import app
from controller.app.models import Run
from controller.app.project_docs import ProjectBrief, render_brief, render_srs
from controller.app.team.base import RunContext
from controller.app.team.mock import MockTeam
from controller.tests.integration.test_team_loop import drain

pytestmark = pytest.mark.integration
client = TestClient(app, headers={"X-SDLC-Client": "web"})

BRIEF = {"title": "Attendance", "summary": "Employees check in with a camera; HR sees attendance.",
         "users": "- EMPLOYEE: checks in and out\n- HR: manages employees and reads reports",
         "features": "- Check in with the camera\n- Attendance report for HR"}


def post(path: str, body: dict | None = None, status: int = 200) -> dict:
    response = client.post(path, json=body or {})
    assert response.status_code == status, response.text
    return response.json()


def srs_document(run_id: str) -> dict:
    detail = client.get(f"/api/runs/{run_id}").json()
    return [d for d in detail["documents"] if d["kind"] == "srs"][-1]


def test_srs_run_goes_from_brief_to_a_pull_request(db, sample_repo):
    project = post("/api/projects", {"key": "task-board", "repo_url": str(sample_repo)}, status=201)
    assert {"name": "SRS.md", "status": "warn"}.items() <= next(
        i for i in project["report"] if i["name"] == "SRS.md").items()

    run = post("/api/runs", {"project": "task-board", "brief": BRIEF, "idempotency_key": str(uuid.uuid4())},
               status=201)
    assert run["kind"] == "srs" and run["title"] == "SRS: Attendance"
    drain()

    assert [(i["role"], i["action"]) for i in client.get("/api/inbox").json()] == [
        ("product_owner", "waiting_spec_approval")]
    srs = srs_document(run["id"])
    assert srs["content"].startswith("# Attendance - Software Requirements Specification (SRS)")
    for section in ("## 1. Introduction", "## 2. Conceptual Domain Model", "## 3. Functional Requirements (FR)",
                    "## 6. Business Flows", "## 7. Features and User Stories", "## 8. External Dependencies"):
        assert section in srs["content"]
    assert "#### 7.1.1 CIW-US-01: Check in with the camera (EMPLOYEE)" in srs["content"]
    assert "| CIW-FR-01 |" in srs["content"]

    broken = client.post(f"/api/runs/{run['id']}/revise-spec", json={"content": "# SRS\n\nno sections"})
    assert broken.status_code == 409 and "section '1. Introduction' is missing" in broken.json()["detail"]
    edited = srs["content"].replace("Employees check in with a camera", "Employees check in with a face camera")
    post(f"/api/runs/{run['id']}/revise-spec", {"content": edited, "reason": "name the camera"})
    srs = srs_document(run["id"])
    assert srs["version"] == 2 and srs["author_kind"] == "human"

    post(f"/api/runs/{run['id']}/approve-spec", {"document_id": srs["id"], "sha256": srs["sha256"]})
    drain()
    detail = client.get(f"/api/runs/{run['id']}").json()
    assert detail["status"] == "WAITING_PR_APPROVAL" and detail["pull_request"]["simulated"]
    assert detail["plans"] == [] and detail["tasks"] == []
    with session_scope() as session:
        worktree = session.get(Run, uuid.UUID(run["id"])).worktree_path
    committed = subprocess.run(["git", "-C", worktree, "show", "HEAD~1:SRS.md"], capture_output=True, text=True,
                               check=True).stdout
    assert committed == edited
    report = [d for d in detail["documents"] if d["kind"] == "report"][-1]["content"]
    assert "| SRS.md | v2 |" in report

    detail = post(f"/api/runs/{run['id']}/pr-decision", {"decision": "approve"})
    assert detail["status"] == "COMPLETED"


def test_srs_rejection_sends_the_reason_back_to_the_business_analyst(db, sample_repo):
    post("/api/projects", {"key": "task-board", "repo_url": str(sample_repo)}, status=201)
    run = post("/api/runs", {"project": "task-board", "brief": BRIEF, "idempotency_key": str(uuid.uuid4())},
               status=201)
    drain()
    post(f"/api/runs/{run['id']}/reject-spec", {"reason": "split the report by month"})
    seen = []

    class Recording:
        def __getattr__(self, name):
            return getattr(self.team, name)

        def write_srs(self, context):
            seen.append(context.feedback)
            return self.team.write_srs(context)

    recording = Recording()
    recording.team = MockTeam()
    drain(recording)
    assert seen == [["Reviewer asked for changes: split the report by month"]]
    assert srs_document(run["id"])["version"] == 2


def test_blocking_question_in_the_brief_waits_for_the_product_owner(db, sample_repo):
    post("/api/projects", {"key": "task-board", "repo_url": str(sample_repo)}, status=201)
    brief = {**BRIEF, "constraints": "- [blocking] Which camera model?"}
    run = post("/api/runs", {"project": "task-board", "brief": brief, "idempotency_key": str(uuid.uuid4())},
               status=201)
    drain()
    detail = client.get(f"/api/runs/{run['id']}").json()
    assert detail["status"] == "NEEDS_CLARIFICATION"
    question = detail["clarifications"][0]
    post(f"/api/runs/{run['id']}/answers", {"answers": {question["id"]: "Hikvision DS-K1T"}})
    drain()
    assert "Hikvision DS-K1T" in srs_document(run["id"])["content"]


def test_a_run_takes_either_an_intent_or_a_brief(db, sample_repo):
    post("/api/projects", {"key": "task-board", "repo_url": str(sample_repo)}, status=201)
    response = client.post("/api/runs", json={"project": "task-board", "idempotency_key": str(uuid.uuid4())})
    assert response.status_code == 422


ARCHITECTURE = {"title": "Attendance", "stack": "Node 20, PostgreSQL", "deployment": "One VM on the office network",
                "integrations": "", "notes": ""}


def with_srs(sample_repo) -> str:
    """Put a rendered SRS on main, as if the SRS pull request had been merged."""
    context = RunContext(title="Attendance", policy=None, agents_md="", review_md=None,
                         intent_md=render_brief(ProjectBrief(**BRIEF)), files=[])
    content = render_srs(MockTeam().write_srs(context), author="t")
    (sample_repo / "SRS.md").write_text(content)
    for args in (["add", "SRS.md"], ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "--quiet", "-m", "srs"]):
        subprocess.run(["git", "-C", str(sample_repo), *args], check=True)
    return content


def test_sds_needs_the_srs_first(db, sample_repo):
    post("/api/projects", {"key": "task-board", "repo_url": str(sample_repo)}, status=201)
    response = client.post("/api/runs", json={"project": "task-board", "architecture": ARCHITECTURE,
                                              "idempotency_key": str(uuid.uuid4())})
    assert response.status_code == 409 and "no SRS.md" in response.json()["detail"]


def test_sds_run_designs_every_srs_story_and_waits_for_the_tech_lead(db, sample_repo):
    with_srs(sample_repo)
    project = post("/api/projects", {"key": "task-board", "repo_url": str(sample_repo)}, status=201)
    assert next(i for i in project["report"] if i["name"] == "SRS.md")["detail"] == "present; 2 user stories in section 7"
    assert next(i for i in project["report"] if i["name"] == "SDS.md")["status"] == "warn"
    run = post("/api/runs", {"project": "task-board", "architecture": ARCHITECTURE,
                             "idempotency_key": str(uuid.uuid4())}, status=201)
    assert run["kind"] == "sds"
    drain()
    assert [(i["role"], i["action"]) for i in client.get("/api/inbox").json()] == [
        ("tech_lead", "waiting_spec_approval")]
    detail = client.get(f"/api/runs/{run['id']}").json()
    sds = [d for d in detail["documents"] if d["kind"] == "sds"][-1]
    assert sds["content"].startswith("# Attendance - Software Design Specification (SDS)")
    assert "#### 5.1.1 CIW-US-01: Check in with the camera (EMPLOYEE)" in sds["content"]
    assert "Let EMPLOYEE check in with the camera." in sds["content"]
    assert "| `API-CIW-01` | `GET /api/v1/ciw` | CIW |" in sds["content"]
    assert "| Application | Node 20, PostgreSQL |" in sds["content"]
    assert sds["findings"] == ["SRS entity Check has no row in SDS §2.1 traceability",
                               "SRS entity Attendance has no row in SDS §2.1 traceability"]

    dropped = sds["content"].replace("#### 5.1.1 CIW-US-01", "#### 5.1.1 XX-US-09")
    response = client.post(f"/api/runs/{run['id']}/revise-spec", json={"content": dropped})
    assert response.status_code == 409 and "no entry for SRS stories CIW-US-01" in response.json()["detail"]

    post(f"/api/runs/{run['id']}/approve-spec", {"document_id": sds["id"], "sha256": sds["sha256"]})
    drain()
    detail = client.get(f"/api/runs/{run['id']}").json()
    assert detail["status"] == "WAITING_PR_APPROVAL"
    assert [a["role"] for a in detail["approvals"]] == ["tech_lead"]
    with session_scope() as session:
        worktree = session.get(Run, uuid.UUID(run["id"])).worktree_path
    committed = subprocess.run(["git", "-C", worktree, "show", "HEAD~1:SDS.md"], capture_output=True, text=True,
                               check=True).stdout
    assert committed == sds["content"]
