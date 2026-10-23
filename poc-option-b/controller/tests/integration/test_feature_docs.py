"""A feature run that delivers one SRS story writes the company layout under specs/NNN-slug/."""

import subprocess
import uuid

from fastapi.testclient import TestClient
import pytest

from controller.app.db import session_scope
from controller.app.main import app
from controller.app.models import Run
from controller.app.project_docs import ArchitectureBrief, render_architecture_brief, render_sds, srs_features
from controller.app.team.base import RunContext
from controller.app.team.mock import MockTeam
from controller.tests.integration.test_srs_flow import post, with_srs
from controller.tests.integration.test_team_loop import TICKS, drain

pytestmark = pytest.mark.integration
client = TestClient(app, headers={"X-SDLC-Client": "web"})


def with_sds(sample_repo, srs: str) -> None:
    brief = render_architecture_brief(ArchitectureBrief(title="Attendance", stack="Node 20"))
    context = RunContext(title="Attendance", policy=None, agents_md="", review_md=None, intent_md=brief, files=[],
                         srs_md=srs)
    (sample_repo / "SDS.md").write_text(render_sds(MockTeam().write_sds(context), srs_features(srs),
                                                   product="Attendance", author="t"))
    for args in (["add", "SDS.md"], ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "--quiet", "-m", "sds"]):
        subprocess.run(["git", "-C", str(sample_repo), *args], check=True)


def show(worktree: str, path: str) -> str:
    return subprocess.run(["git", "-C", worktree, "show", f"HEAD:{path}"], capture_output=True, text=True,
                          check=True).stdout


def test_feature_run_needs_a_story_once_the_project_has_an_srs(db, sample_repo):
    with_srs(sample_repo)
    post("/api/projects", {"key": "task-board", "repo_url": str(sample_repo)}, status=201)
    intent = {"title": "Check in", "problem": "No check-in.", "outcome": "- Employees check in"}
    response = client.post("/api/runs", json={"project": "task-board", "intent": intent,
                                              "idempotency_key": str(uuid.uuid4())})
    assert response.status_code == 409 and "pick the user story" in response.json()["detail"]
    response = client.post("/api/runs", json={"project": "task-board", "intent": {**intent, "story": "ZZ-US-01"},
                                              "idempotency_key": str(uuid.uuid4())})
    assert response.status_code == 409 and "ZZ-US-01 is not a user story" in response.json()["detail"]
    stories = client.get("/api/projects/task-board/stories").json()
    assert [(s["id"], s["run"]) for f in stories["features"] for s in f["stories"]] == [
        ("CIW-US-01", None), ("ARF-US-01", None)]


def test_story_run_writes_spec_plan_tasks_and_test_cases_in_the_feature_folder(db, sample_repo):
    srs = with_srs(sample_repo)
    with_sds(sample_repo, srs)
    post("/api/projects", {"key": "task-board", "repo_url": str(sample_repo)}, status=201)
    intent = {"title": "Check in with the camera", "problem": "Employees cannot check in.",
              "outcome": "- An employee who faces the camera is checked in\n- An unknown face is refused",
              "story": "CIW-US-01"}
    run = post("/api/runs", {"project": "task-board", "intent": intent, "idempotency_key": str(uuid.uuid4())},
               status=201)
    drain()
    detail = client.get(f"/api/runs/{run['id']}").json()
    spec = [d for d in detail["documents"] if d["kind"] == "spec"][-1]
    assert spec["content"].startswith("## CIW-US-01: Check in with the camera (EMPLOYEE)")
    assert "> **Feature group:** 7.1 Check in with the camera" in spec["content"]
    assert "- **AC-01 — An employee who faces:**  \n  **Given** the product as it is,  \n" in spec["content"]
    post(f"/api/runs/{run['id']}/approve-spec", {"document_id": spec["id"], "sha256": spec["sha256"], "checked": TICKS})
    drain()

    with session_scope() as session:
        worktree = session.get(Run, uuid.UUID(run["id"])).worktree_path
    folder = "specs/001-check-in-with-the-camera"
    spec_md = show(worktree, f"{folder}/spec.md")
    assert spec_md.startswith("# Feature: Check in with the camera\n\n> **Prefix:** `CIW`")
    assert "## CIW-US-01: Check in with the camera (EMPLOYEE)" in spec_md
    assert "Business Analyst notes" not in spec_md and spec_md.rstrip().endswith("---")
    requirements = show(worktree, f"{folder}/checklists/requirements.md")
    assert "## CIW-US-01: Check in with the camera" in requirements and "### Notes" in requirements
    assert "story_prefix: CIW" in show(worktree, f"{folder}/sdlc.yaml")
    assert f"> Spec: [{folder}/spec.md]({folder}/spec.md)" in show(worktree, "SRS.md")

    plan = client.get(f"/api/runs/{run['id']}").json()["plans"][-1]
    post(f"/api/runs/{run['id']}/approve-plan", {"plan_id": plan["id"], "sha256": plan["sha256"], "checked": TICKS})
    tests = [d for d in client.get(f"/api/runs/{run['id']}").json()["documents"] if d["kind"] == "tests"][-1]
    assert tests["content"].startswith("## CIW-US-01: Check in with the camera (EMPLOYEE)")
    post(f"/api/runs/{run['id']}/approve-tests", {"document_id": tests["id"], "sha256": tests["sha256"], "checked": TICKS})
    drain()
    plan_md = show(worktree, f"{folder}/plan.md")
    assert plan_md.startswith("# Plan: Check in with the camera (CIW)")
    assert "### Pre-flight: Spec Review Findings" in plan_md and "**Open tasks**" in plan_md
    cases = show(worktree, f"{folder}/test_cases.md")
    assert "### Coverage Matrix" in cases and "| AC-01 — An employee who faces | API | TC-01 |" in cases
    assert "### TC-01:" in cases and "- **AC**: AC-01" in cases
    tasks = show(worktree, f"{folder}/tasks.md")
    assert "## Phase 1: CIW-US-01: Check in with the camera" in tasks
    assert "- [x] T001 [CIW-US-01] Write tests TC-01" in tasks  # ticked before the PR: the mock tasks passed
    assert f"> Plan: [{folder}/plan.md]({folder}/plan.md)" in show(worktree, "SDS.md")
    detail = client.get(f"/api/runs/{run['id']}").json()
    assert detail["status"] == "WAITING_PR_APPROVAL"
    stories = client.get("/api/projects/task-board/stories").json()
    assert stories["features"][0]["stories"][0]["run"] == {"id": run["id"], "status": "WAITING_PR_APPROVAL"}


def test_the_project_constitution_reaches_every_role(db, sample_repo):
    rules = sample_repo / ".specify" / "memory" / "constitution.md"
    rules.parent.mkdir(parents=True)
    rules.write_text("# Constitution\n\n- AR-01: business rules live in src/tasks.js\n")
    for args in (["add", "."], ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "--quiet", "-m", "rules"]):
        subprocess.run(["git", "-C", str(sample_repo), *args], check=True)
    project = post("/api/projects", {"key": "task-board", "repo_url": str(sample_repo)}, status=201)
    item = next(i for i in project["report"] if i["name"] == "constitution")
    assert item["status"] == "ok" and item["detail"].startswith(".specify/memory/constitution.md")
    seen = []

    class Recording(MockTeam):
        def specify(self, context):
            seen.append(context.constitution_md)
            return super().specify(context)

    intent = {"title": "Due dates", "problem": "No deadlines.", "outcome": "- Optional due date"}
    post("/api/runs", {"project": "task-board", "intent": intent, "idempotency_key": str(uuid.uuid4())}, status=201)
    drain(Recording())
    assert seen == ["# Constitution\n\n- AR-01: business rules live in src/tasks.js\n"]


def test_the_feature_form_suggests_outcomes_from_the_srs_and_sds_of_the_story(db, sample_repo):
    from controller.tests.integration.test_srs_flow import post, with_srs
    from fastapi.testclient import TestClient
    from controller.app.main import app
    client = TestClient(app, headers={"X-SDLC-Client": "web"})
    with_sds(sample_repo, with_srs(sample_repo))
    post("/api/projects", {"key": "task-board", "repo_url": str(sample_repo)}, status=201)
    drafted = client.get("/api/projects/task-board/stories/CIW-US-01/outcomes").json()
    assert drafted["story"] == "CIW-US-01" and drafted["suggestions"]
    assert all(s["text"] and s["source"] for s in drafted["suggestions"])
    answered = client.post("/api/projects/task-board/stories/CIW-US-01/outcomes/ai").json()
    assert answered["suggestions"] and answered["notes"] == "mock"
    assert client.get("/api/projects/task-board/stories/XX-US-99/outcomes").status_code == 404


def test_the_backlog_orders_the_stories_and_opens_a_ready_one(db, sample_repo):
    srs = with_srs(sample_repo)
    with_sds(sample_repo, srs)
    post("/api/projects", {"key": "task-board", "repo_url": str(sample_repo)}, status=201)
    empty = client.get("/api/projects/task-board/backlog").json()
    assert empty["status"] is None and [s["story"] for s in empty["not_in_backlog"]] == ["CIW-US-01", "ARF-US-01"]
    drafted = client.post("/api/projects/task-board/backlog/propose", json={}).json()
    assert drafted["status"] == "draft" and drafted["problems"] == [] and drafted["not_in_backlog"] == []
    assert [(i["story"], i["state"], i["wave"]) for i in drafted["items"]] == [("CIW-US-01", "ready", 1),
                                                                              ("ARF-US-01", "ready", 1)]
    items = [{**{k: i[k] for k in ("story", "depends_on", "reason", "hold", "done")}} for i in drafted["items"]]
    items[1]["depends_on"] = ["CIW-US-01"]
    cyclic = [{**items[0], "depends_on": ["ARF-US-01"]}, items[1]]
    saved = client.put("/api/projects/task-board/backlog", json={"items": cyclic, "version": drafted["version"]}).json()
    assert saved["problems"] == ["dependency cycle: CIW-US-01 → ARF-US-01 → CIW-US-01"]
    refused = client.post("/api/projects/task-board/backlog/approve", json={"version": saved["version"]})
    assert refused.status_code == 409 and "dependency cycle" in refused.json()["detail"]
    stale = client.put("/api/projects/task-board/backlog", json={"items": items, "version": drafted["version"]})
    assert stale.status_code == 409 and "changed" in stale.json()["detail"]
    saved = client.put("/api/projects/task-board/backlog", json={"items": items, "version": saved["version"]}).json()
    assert [(i["story"], i["state"], i["wave"]) for i in saved["items"]] == [("CIW-US-01", "ready", 1),
                                                                            ("ARF-US-01", "blocked", 2)]
    approved = client.post("/api/projects/task-board/backlog/approve", json={"version": saved["version"]}).json()
    assert approved["status"] == "approved" and approved["approved_by"]
    assert client.post("/api/projects/task-board/backlog/ARF-US-01/start").status_code == 409  # waits for CIW-US-01
    run = client.post("/api/projects/task-board/backlog/CIW-US-01/start")
    assert run.status_code == 201
    with session_scope() as session:
        from controller.app.documents import latest_document
        intent = latest_document(session, uuid.UUID(run.json()["id"]), "intent").data
    assert intent["story"] == "CIW-US-01" and intent["outcome"].startswith("- ")
    after = client.get("/api/projects/task-board/backlog").json()
    assert after["items"][0]["state"] == "running" and after["items"][0]["run"]["id"] == run.json()["id"]
    assert client.post("/api/projects/task-board/backlog/CIW-US-01/start").status_code == 409  # already running
