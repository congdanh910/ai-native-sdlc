"""The preview of a run: started and stopped on request, stopped when its time is up, never stopping the run."""

import uuid

from fastapi.testclient import TestClient
import pytest

from controller.app import coordinator
from controller.app.coordinator import expire_previews
from controller.app.db import session_scope
from controller.app.main import app
from controller.tests.conftest import git
from controller.tests.integration.test_srs_flow import post
from controller.tests.integration.test_team_loop import drain

pytestmark = pytest.mark.integration
client = TestClient(app, headers={"X-SDLC-Client": "web"})
INTENT = {"title": "Due dates", "problem": "Tasks have no due date.", "outcome": "- Tasks can have a due date"}
PREVIEW = ("preview:\n  compose: .sdlc/preview.compose.yml\n  service: web\n  port: 8080\n  health_path: /health\n"
           "  accounts:\n    - {role: ADMIN, username: admin@example.test, password: Demo-123}\n")


def start_run(sample_repo, with_preview: bool, compose: str = "services:\n  web:\n    build: ./src\n") -> str:
    if with_preview:
        config = sample_repo / ".sdlc" / "project.yaml"
        config.write_text(config.read_text() + PREVIEW)
        if compose:
            (sample_repo / ".sdlc" / "preview.compose.yml").write_text(compose)
        git(sample_repo, "add", ".")
        git(sample_repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "--quiet", "-m", "preview")
    post("/api/projects", {"key": "task-board", "repo_url": str(sample_repo)}, status=201)
    run = post("/api/runs", {"project": "task-board", "intent": INTENT, "idempotency_key": str(uuid.uuid4())}, status=201)
    drain()
    return run["id"]


def test_a_project_without_a_preview_has_none(db, sample_repo):
    run_id = start_run(sample_repo, with_preview=False)
    assert client.get(f"/api/runs/{run_id}").json()["preview"] is None
    refused = client.post(f"/api/runs/{run_id}/preview", json={"action": "start"})
    assert refused.status_code == 409 and "no preview" in refused.text


def test_a_preview_starts_stops_and_expires_without_moving_the_run(db, sample_repo, monkeypatch):
    run_id = start_run(sample_repo, with_preview=True)
    before = client.get(f"/api/runs/{run_id}").json()
    assert before["preview"]["status"] == "stopped" and before["preview"]["accounts"][0]["role"] == "ADMIN"
    calls = []
    monkeypatch.setattr(coordinator, "_run_preview", lambda loaded: calls.append(loaded.extra) or (
        {"preview": {"status": "running", "url": "http://127.0.0.1:9100/", "port": 9100,
                     "started_at": "2026-10-21T00:00:00+00:00", "expires_at": "2026-10-21T01:00:00+00:00"}}
        if loaded.extra["start"] else {"preview": {"status": "stopped", "detail": "stopped"}}))
    after = post(f"/api/runs/{run_id}/preview", {"action": "start"})
    assert after["preview"]["status"] == "starting" and after["status"] == before["status"]
    drain()
    state = client.get(f"/api/runs/{run_id}").json()
    assert state["preview"]["status"] == "running" and state["status"] == before["status"]
    assert calls[0]["preview"]["service"] == "web" and calls[0]["taken"] == []
    with session_scope() as session:  # its hour is over: the worker's poll stops it
        assert expire_previews(session) == 1
    drain()
    assert client.get(f"/api/runs/{run_id}").json()["preview"]["status"] == "stopped"


def test_a_failing_preview_job_leaves_the_run_where_it_was(db, sample_repo, monkeypatch):
    run_id = start_run(sample_repo, with_preview=True)
    status = client.get(f"/api/runs/{run_id}").json()["status"]

    def broken(loaded):
        raise RuntimeError("docker is not running")
    monkeypatch.setattr(coordinator, "_run_preview", broken)
    post(f"/api/runs/{run_id}/preview", {"action": "start"})
    for _ in range(4):  # the job is retried, then given up
        drain()
        with session_scope() as session:
            from controller.app.models import Execution
            for execution in session.query(Execution).filter(Execution.kind == "preview", Execution.status == "queued"):
                execution.available_at = None
    state = client.get(f"/api/runs/{run_id}").json()
    assert state["status"] == status and state["preview"]["status"] == "failed"
    assert "docker is not running" in state["preview"]["detail"]


def test_a_preview_waits_for_the_code_its_compose_file_builds(db, sample_repo):
    run_id = start_run(sample_repo, with_preview=True,
                       compose="services:\n  web:\n    build: ./web\n  api:\n    build:\n      context: ./backend\n")
    preview = client.get(f"/api/runs/{run_id}").json()["preview"]
    assert preview["missing"] == ["web/", "backend/"] and preview["status"] == "unavailable"
    assert "Available once the code exists: web/, backend/" in preview["detail"]
    refused = client.post(f"/api/runs/{run_id}/preview", json={"action": "start"})
    assert refused.status_code == 409 and "nothing to preview yet: web/, backend/" in refused.text
