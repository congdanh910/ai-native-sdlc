"""The foundation run: onboarding files from the SDS through their gate and the environment check, then the skeleton."""

import uuid

from fastapi.testclient import TestClient
import pytest

from controller.app.coordinator import artifact
from controller.app.db import session_scope
from controller.app.main import app
from controller.app.models import Run
from controller.tests.integration.test_feature_docs import show, with_sds
from controller.tests.integration.test_srs_flow import post, with_srs
from controller.tests.integration.test_team_loop import TICKS, drain

pytestmark = pytest.mark.integration
client = TestClient(app, headers={"X-SDLC-Client": "web"})


def detail(run_id: str) -> dict:
    return client.get(f"/api/runs/{run_id}").json()


def test_a_foundation_needs_the_sds(db, sample_repo):
    with_srs(sample_repo)
    post("/api/projects", {"key": "task-board", "repo_url": str(sample_repo)}, status=201)
    response = client.post("/api/runs", json={"project": "task-board", "foundation": {"title": "Attendance"},
                                              "idempotency_key": str(uuid.uuid4())})
    assert response.status_code == 409 and "no SDS.md" in response.json()["detail"]


def test_the_onboarding_files_pass_their_gate_then_the_run_builds_the_skeleton(db, sample_repo):
    with_sds(sample_repo, with_srs(sample_repo))
    post("/api/projects", {"key": "task-board", "repo_url": str(sample_repo)}, status=201)
    run = post("/api/runs", {"project": "task-board", "idempotency_key": str(uuid.uuid4()),
                             "foundation": {"title": "Attendance", "notes": "No CI yet."}}, status=201)
    assert run["title"] == "Foundation: Attendance"
    drain()
    state = detail(run["id"])
    assert state["status"] == "WAITING_ONBOARDING_APPROVAL" and state["waiting"]["role"] == "tech_lead"
    intent = [d for d in state["documents"] if d["kind"] == "intent"][-1]["content"]
    assert "## Design reference (from SDS.md)" in intent and "No CI yet." in intent
    onboarding = [d for d in state["documents"] if d["kind"] == "onboarding"][-1]
    assert "## `AGENTS.md` (changed)" in onboarding["content"] and "## `.gitignore` (new)" in onboarding["content"]
    assert "````diff" in onboarding["content"]
    assert {c["id"]: c["ok"] for c in state["checklist"]["controller"]} == {"files": True}

    # the Tech Lead asks for changes: the Architect writes v2 with the reason, and the gate shows the revision
    refused = client.post(f"/api/runs/{run['id']}/onboarding/redo", json={"reason": ""})
    assert refused.status_code == 409
    post(f"/api/runs/{run['id']}/onboarding/redo", {"reason": "Lint the frontend too"})
    drain()
    state = detail(run["id"])
    onboarding = [d for d in state["documents"] if d["kind"] == "onboarding"][-1]
    assert onboarding["version"] == 2 and onboarding["revision"]["feedback"] == ["Lint the frontend too"]

    # approved: committed to the run's branch, the environment is tried, the run follows the new files
    post(f"/api/runs/{run['id']}/onboarding/approve", {"document_id": onboarding["id"], "checked": TICKS})
    drain()
    state = detail(run["id"])
    assert state["status"] == "WAITING_SPEC_APPROVAL", state["reason"]
    with session_scope() as session:
        run_id = uuid.UUID(run["id"])
        assert artifact(session, run_id, "agents_md").startswith("# Foundation: Attendance")
        assert artifact(session, run_id, "onboarding_commit")
        worktree = session.get(Run, run_id).worktree_path
    assert "Mock Architect" in show(worktree, "AGENTS.md") and "node_modules/" in show(worktree, ".gitignore")
    assert [a["stage"] for a in state["approvals"]].count("onboarding") == 2  # the reject and the approval


def test_onboarding_files_keep_the_project_settings_and_must_load():
    from controller.app.foundation import Onboarding, onboarding_files, sds_excerpt
    current = ("schema_version: 1\nproject: attendance\nvalidation_image: node-20\nvalidation:\n"
               "  - name: unit-tests\n    run: [node, --test]\npaths:\n  allowed: [src/**]\n  tests: [test/**]\n"
               "executors: [codex]\ndefault_executor: codex\nlimits:\n  thinking_timeout_minutes: 20\n"
               "stages:\n  options: skip\n")
    onboarding = Onboarding.model_validate({
        "agents_md": "# Attendance\n\nPython 3.13 and React 19.\n", "dockerfile": "FROM python:3.13-slim\n",
        "validation": [{"name": "backend-tests", "run": ["sh", "-c", "cd backend && pytest"]}],
        "paths": {"allowed": ["backend/**", "web/**"], "tests": ["backend/tests/**", "web/src/**/*.test.tsx"]},
        "environment": {"services": [{"name": "postgres", "image": "postgres:17-alpine", "ready": ["pg_isready"]}],
                        "env": {"DATABASE_URL": "postgresql://app:app@postgres:5432/app"}},
        "limits": {"max_tasks": 8, "max_changed_files": 30}})
    files = onboarding_files(onboarding, current)
    config = files[".sdlc/project.yaml"]
    assert "validation_image" not in config and "dockerfile: .sdlc/validation.Dockerfile" in config
    assert "thinking_timeout_minutes: 20" in config and "options: skip" in config and "project: attendance" in config
    assert files[".sdlc/validation.Dockerfile"] == "FROM python:3.13-slim\n" and "REVIEW.md" not in files
    broken = onboarding.model_copy(update={"dockerfile": "", "validation_image": ""})
    with pytest.raises(ValueError, match="validation_image, or an environment.dockerfile"):
        onboarding_files(broken, current)
    sds = "## 4. Architecture\n### 4.3 Development View\nstack\n#### 4.3.5 Repository Layout\nlayout\n### 4.4 Process\nx\n" \
          "## 7. Security Design <a id=\"7\"></a>\n### 7.1 User Authentication\nsign-in\n## 8. NFR\nn\n"
    assert sds_excerpt(sds, ("4.3.5", "7.1")) == "#### 4.3.5 Repository Layout\nlayout\n### 7.1 User Authentication\nsign-in"
    assert "### 4.4" not in sds_excerpt(sds, ("4.3", "7.")) and "## 7. Security Design\n" in sds_excerpt(sds, ("7.",))


def test_a_broken_environment_goes_back_to_the_architect_then_to_a_human(db, sample_repo, monkeypatch):
    from controller.app import coordinator
    with_sds(sample_repo, with_srs(sample_repo))
    post("/api/projects", {"key": "task-board", "repo_url": str(sample_repo)}, status=201)
    run = post("/api/runs", {"project": "task-board", "idempotency_key": str(uuid.uuid4()),
                             "foundation": {"title": "Attendance"}}, status=201)
    drain()
    monkeypatch.setattr(coordinator, "_try_environment",
                        lambda loaded: {"ok": False, "detail": "service-postgres failed: not ready after 60s"})
    seen = []
    for _ in range(3):  # limits.max_remediations of the example project is 2
        onboarding = [d for d in detail(run["id"])["documents"] if d["kind"] == "onboarding"][-1]
        post(f"/api/runs/{run['id']}/onboarding/approve", {"document_id": onboarding["id"], "checked": TICKS})
        drain()
        state = detail(run["id"])
        seen.append(state["status"])
        if state["status"] != "WAITING_ONBOARDING_APPROVAL":
            break
        revision = [d for d in state["documents"] if d["kind"] == "onboarding"][-1]["revision"]
        assert revision["feedback"] == ["service-postgres failed: not ready after 60s"]
    assert seen == ["WAITING_ONBOARDING_APPROVAL", "WAITING_ONBOARDING_APPROVAL", "NEEDS_HUMAN"]
    assert "validation environment does not work" in state["reason"]
