from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from controller.app.config import Settings, get_settings
from controller.app.db import get_session
from controller.app.main import app
from controller.app.models import Base, Execution, Plan, Run, Task
from controller.app.schemas import PlanDecision, PlanPayload, PlanRevision, RunCreate, VersionedDecision
from controller.app.services import approve_plan, cancel_run, create_run, record_plan, revise_plan
from controller.app.state_machine import ConflictError, transition, utc_now
from controller.app.worker import claim_next_execution, complete_mock_execution, reconcile_expired_executions


@pytest.fixture
def db_session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


def make_run(session: Session) -> Run:
    run = create_run(
        session,
        RunCreate(
            repo_id="owner/repo", base_commit="a" * 40,
            requirement="Add a small feature", idempotency_key="run-1",
        ),
        frozenset({"owner/repo"}),
    )
    session.flush()
    return run


def make_plan(session: Session, run: Run):
    transition(session, run, "ANALYZING")
    transition(session, run, "PLANNING")
    plan = record_plan(
        session, run.id,
        PlanPayload.model_validate({"tasks": [
            {"id": "first", "description": "Implement", "allowed_paths": ["src"],
             "acceptance_criteria": ["Works"], "dependencies": []},
            {"id": "second", "description": "Document", "allowed_paths": ["docs"],
             "acceptance_criteria": ["Documented"], "dependencies": ["first"]},
        ]}),
    )
    session.flush()
    return plan


def test_plan_approval_is_version_bound_and_dispatches_once(db_session: Session) -> None:
    run = make_run(db_session)
    plan = make_plan(db_session, run)
    stale = PlanDecision(
        plan_id=plan.id, plan_sha256=plan.sha256,
        expected_run_version=run.version - 1, actor="human",
    )
    with pytest.raises(ConflictError):
        approve_plan(db_session, run.id, stale)
    assert db_session.scalars(select(Task)).all() == []

    approved = stale.model_copy(update={"expected_run_version": run.version})
    approve_plan(db_session, run.id, approved)
    db_session.commit()
    assert run.status == "READY"
    assert len(db_session.scalars(select(Task)).all()) == 2
    assert len(db_session.scalars(select(Execution)).all()) == 1
    with pytest.raises(ConflictError):
        approve_plan(db_session, run.id, approved)


def test_plan_revision_keeps_history_and_requires_latest_checksum(db_session: Session) -> None:
    run = make_run(db_session)
    first = make_plan(db_session, run)
    old_run_version = run.version
    revision = PlanRevision(
        expected_run_version=old_run_version, actor="reviewer",
        reason="Move business logic into the model module",
        plan=PlanPayload.model_validate({"tasks": [{
            "id": "filter", "description": "Pure filter model", "dependencies": [],
            "allowed_paths": ["src/tasks.js"], "acceptance_criteria": ["Filter is pure"],
        }]}),
    )
    second = revise_plan(db_session, run.id, revision)
    assert run.status == "WAITING_PLAN_APPROVAL"
    assert run.version == old_run_version + 1
    assert second.version == first.version + 1
    assert len(db_session.scalars(select(Plan).where(Plan.run_id == run.id)).all()) == 2
    with pytest.raises(ConflictError):
        revise_plan(db_session, run.id, revision)
    with pytest.raises(ConflictError):
        approve_plan(db_session, run.id, PlanDecision(
            plan_id=first.id, plan_sha256=first.sha256,
            expected_run_version=run.version, actor="human",
        ))
    approve_plan(db_session, run.id, PlanDecision(
        plan_id=second.id, plan_sha256=second.sha256,
        expected_run_version=run.version, actor="human",
    ))
    assert [task.external_task_id for task in db_session.scalars(select(Task))] == ["filter"]


def test_plan_revision_api_requires_token_and_returns_new_checksum(db_session: Session) -> None:
    run = make_run(db_session)
    first = make_plan(db_session, run)
    def session_override():
        yield db_session

    app.dependency_overrides[get_session] = session_override
    app.dependency_overrides[get_settings] = lambda: Settings(
        database_url="sqlite://", controller_token="local-test-token", allowed_repos="owner/repo"
    )
    try:
        client = TestClient(app)
        payload = {
            "expected_run_version": run.version, "actor": "reviewer", "reason": "Fix path",
            "plan": {"tasks": [{
                "id": "filter", "description": "Add pure filter", "dependencies": [],
                "allowed_paths": ["src/tasks.js"], "acceptance_criteria": ["Search works"],
            }]},
        }
        path = f"/api/runs/{run.id}/revise-plan"
        assert client.post(path, json=payload).status_code == 401
        result = client.post(path, json=payload, headers={"X-Controller-Token": "local-test-token"})
        assert result.status_code == 200
        assert result.json()["status"] == "WAITING_PLAN_APPROVAL"
        assert result.json()["plan_version"] == 2
        assert result.json()["plan_sha256"] != first.sha256
    finally:
        app.dependency_overrides.clear()


def test_dashboard_lists_and_creates_pinned_pilot_runs(db_session: Session) -> None:
    from pathlib import Path
    import json

    manifest = json.loads((Path(__file__).resolve().parents[2] / "fixtures/task-board/benchmark.json").read_text())
    repo = manifest["repository"].removeprefix("https://github.com/").removesuffix(".git")

    def session_override():
        yield db_session

    app.dependency_overrides[get_session] = session_override
    app.dependency_overrides[get_settings] = lambda: Settings(
        database_url="sqlite://", controller_token="local-test-token", allowed_repos=repo,
    )
    try:
        client = TestClient(app)
        assert client.get("/").status_code == 200
        assert client.get("/api/runs").status_code == 401
        headers = {"X-Controller-Token": "local-test-token"}
        features = client.get("/api/pilot/features", headers=headers)
        assert features.status_code == 200
        assert [item["id"] for item in features.json()] == [item["id"] for item in manifest["tasks"]]
        assert client.post("/api/pilot/runs/unknown", headers=headers).status_code == 404
        created = client.post("/api/pilot/runs/archive", headers=headers)
        assert created.status_code == 201
        run = client.get(f"/api/runs/{created.json()['id']}", headers=headers).json()
        assert run["base_commit"] == manifest["base_commit"]
        assert run["requirement"] == features.json()[3]["requirement"]
        listed = client.get("/api/runs", headers=headers)
        assert listed.status_code == 200
        assert listed.json()[0]["id"] == created.json()["id"]
        assert client.get("/api/runs?limit=101", headers=headers).status_code == 422
    finally:
        app.dependency_overrides.clear()


def test_mock_worker_obeys_dependencies_and_reconciles_uncertain_run(db_session: Session) -> None:
    run = make_run(db_session)
    plan = make_plan(db_session, run)
    approve_plan(
        db_session, run.id,
        PlanDecision(plan_id=plan.id, plan_sha256=plan.sha256,
                     expected_run_version=run.version, actor="human"),
    )
    first = claim_next_execution(db_session, "worker-1")
    assert first is not None
    assert claim_next_execution(db_session, "worker-2") is None
    complete_mock_execution(db_session, first.id, "worker-1")
    second = claim_next_execution(db_session, "worker-2")
    assert second is not None and second.id != first.id
    second.lease_until = utc_now() - timedelta(seconds=1)
    db_session.flush()

    assert reconcile_expired_executions(db_session) == [second.id]
    assert run.status == "NEEDS_HUMAN"
    assert second.status == "UNKNOWN"
    assert claim_next_execution(db_session, "worker-3") is None


def test_create_run_idempotency_and_api_auth(db_session: Session) -> None:
    def session_override():
        yield db_session

    app.dependency_overrides[get_session] = session_override
    app.dependency_overrides[get_settings] = lambda: Settings(
        database_url="sqlite://", controller_token="local-test-token", allowed_repos="owner/repo"
    )
    try:
        client = TestClient(app)
        payload = {
            "repo_id": "owner/repo", "base_commit": "a" * 40,
            "requirement": "Add a small feature", "idempotency_key": "same-key",
        }
        assert client.post("/api/runs", json=payload).status_code == 401
        headers = {"X-Controller-Token": "local-test-token"}
        first = client.post("/api/runs", json=payload, headers=headers)
        duplicate = client.post("/api/runs", json=payload, headers=headers)
        assert first.status_code == 201
        assert duplicate.json()["id"] == first.json()["id"]
        assert client.get(f"/api/runs/{first.json()['id']}", headers=headers).json()["status"] == "RECEIVED"
        changed = client.post("/api/runs", json={**payload, "requirement": "Different"}, headers=headers)
        assert changed.status_code == 409
    finally:
        app.dependency_overrides.clear()


def test_plan_rejects_cycles_and_traversal() -> None:
    with pytest.raises(ValueError):
        PlanPayload.model_validate({"tasks": [
            {"id": "a", "description": "A", "allowed_paths": ["src"],
             "acceptance_criteria": ["A"], "dependencies": ["b"]},
            {"id": "b", "description": "B", "allowed_paths": ["src"],
             "acceptance_criteria": ["B"], "dependencies": ["a"]},
        ]})
    with pytest.raises(ValueError):
        PlanPayload.model_validate({"tasks": [
            {"id": "a", "description": "A", "allowed_paths": ["../secret"],
             "acceptance_criteria": ["A"]},
        ]})


def test_cancel_ready_run_removes_queued_work(db_session: Session) -> None:
    run = make_run(db_session)
    plan = make_plan(db_session, run)
    approve_plan(
        db_session, run.id,
        PlanDecision(plan_id=plan.id, plan_sha256=plan.sha256,
                     expected_run_version=run.version, actor="human"),
    )
    cancel_run(
        db_session, run.id,
        VersionedDecision(expected_run_version=run.version, actor="human", reason="scope changed"),
    )
    db_session.commit()
    assert run.status == "CANCELLED"
    assert [item.status for item in db_session.scalars(select(Task).order_by(Task.external_task_id))] == [
        "CANCELLED", "CANCELLED",
    ]
    assert [item.status for item in db_session.scalars(select(Execution))] == ["CANCELLED"]
    assert claim_next_execution(db_session, "worker") is None


def test_executor_is_pinned_before_worker_claim(db_session: Session) -> None:
    run = create_run(
        db_session,
        RunCreate(
            repo_id="owner/repo", base_commit="a" * 40,
            requirement="Implement feature", idempotency_key="claude-run", executor="claude",
        ),
        frozenset({"owner/repo"}),
    )
    plan = make_plan(db_session, run)
    approve_plan(
        db_session, run.id,
        PlanDecision(plan_id=plan.id, plan_sha256=plan.sha256,
                     expected_run_version=run.version, actor="human"),
    )
    assert claim_next_execution(db_session, "codex-worker", executor_name="codex") is None
    claimed = claim_next_execution(db_session, "claude-worker", executor_name="claude")
    assert claimed is not None
    assert db_session.get(Task, claimed.task_id).executor == "claude"
    with pytest.raises(ConflictError):
        create_run(
            db_session,
            RunCreate(
                repo_id="owner/repo", base_commit="a" * 40,
                requirement="Implement feature", idempotency_key="claude-run", executor="codex",
            ),
            frozenset({"owner/repo"}),
        )
