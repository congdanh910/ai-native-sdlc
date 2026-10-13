import hashlib
import json
import subprocess
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from controller.app.models import Artifact, Base, Plan, Run
from controller.app.reasoning_worker import ReasoningPolicy, analyze_and_plan
from controller.app.schemas import AnalysisResult, PlanPayload, RunCreate
from controller.app.services import create_run


def setup_reasoning(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=repo, check=True)
    context = b"Pinned repo guidance\n"
    (repo / "AGENTS.md").write_bytes(context)
    (repo / "src").mkdir()
    (repo / "src" / "app.js").write_text("const value = 1;\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "base"], cwd=repo, check=True)
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    engine = create_engine(f"sqlite:///{tmp_path / 'reasoning.db'}")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(engine, expire_on_commit=False)
    with sessions() as session:
        run = create_run(session, RunCreate(
            repo_id="owner/repo", base_commit=commit,
            requirement="Add search", idempotency_key="reasoning-demo",
        ), frozenset({"owner/repo"}))
        run_id = run.id
        session.commit()
    policy = ReasoningPolicy(
        repo_path=repo, artifact_root=tmp_path / "artifacts",
        agent_context_sha256=hashlib.sha256(context).hexdigest(),
        requirements_flow_id="requirements-flow", planning_flow_id="planning-flow",
    )
    return engine, sessions, run_id, policy


class FakeClient:
    def __init__(self, *, questions=(), plan_paths=("src",), fail_once=False):
        self.questions = list(questions)
        self.plan_paths = list(plan_paths)
        self.fail_once = fail_once
        self.calls = []

    def run_json_flow(self, flow_id, input_data, result_type, *, session_id):
        from controller.app.integrations.langflow import LangflowUnavailable

        self.calls.append((flow_id, input_data, session_id))
        if self.fail_once:
            self.fail_once = False
            raise LangflowUnavailable("temporary")
        if result_type is AnalysisResult:
            return AnalysisResult(
                summary="Add search", acceptance_criteria=["Search works"],
                questions=self.questions,
            )
        return PlanPayload.model_validate({"tasks": [{
            "id": "search", "description": "Implement search", "dependencies": [],
            "allowed_paths": self.plan_paths, "acceptance_criteria": ["Search works"],
        }]})


def test_analysis_and_plan_persist_artifact_and_wait_for_approval(tmp_path):
    engine, sessions, run_id, policy = setup_reasoning(tmp_path)
    client = FakeClient(fail_once=True)
    delays = []
    analyze_and_plan(sessions, run_id, policy, client, sleep=delays.append)
    with sessions() as session:
        run = session.get(Run, run_id)
        artifact = session.scalar(select(Artifact).where(
            Artifact.run_id == run_id, Artifact.kind == "analysis",
        ))
        context_artifact = session.scalar(select(Artifact).where(
            Artifact.run_id == run_id, Artifact.kind == "agent_context",
        ))
        plan = session.scalar(select(Plan).where(Plan.run_id == run_id))
        assert run.status == "WAITING_PLAN_APPROVAL"
        assert plan.body["tasks"][0]["allowed_paths"] == ["src"]
        data = Path(artifact.storage_path).read_bytes()
        assert hashlib.sha256(data).hexdigest() == artifact.checksum
        assert json.loads(data)["summary"] == "Add search"
        assert context_artifact.checksum == policy.agent_context_sha256
    assert delays == [1]
    assert len(client.calls) == 3
    assert "src/app.js" in client.calls[0][1]["repo_context"]
    assert "src/app.js" in client.calls[-1][1]["repo_context"]
    engine.dispose()


def test_analysis_questions_stop_before_planning(tmp_path):
    engine, sessions, run_id, policy = setup_reasoning(tmp_path)
    client = FakeClient(questions=["Which fields should search cover?"])
    analyze_and_plan(sessions, run_id, policy, client)
    with sessions() as session:
        run = session.get(Run, run_id)
        assert run.status == "NEEDS_HUMAN"
        assert run.failure_category == "AMBIGUOUS_SPEC"
        assert session.scalar(select(Plan).where(Plan.run_id == run_id)) is None
    assert len(client.calls) == 1
    engine.dispose()


def test_protected_model_plan_fails_closed(tmp_path):
    engine, sessions, run_id, policy = setup_reasoning(tmp_path)
    client = FakeClient(plan_paths=[".github"])
    with pytest.raises(Exception, match="protected"):
        analyze_and_plan(sessions, run_id, policy, client)
    with sessions() as session:
        run = session.get(Run, run_id)
        assert run.status == "FAILED"
        assert run.failure_stage == "planning"
        assert session.scalar(select(Plan).where(Plan.run_id == run_id)) is None
    engine.dispose()
