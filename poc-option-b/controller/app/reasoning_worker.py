"""Call Langflow for advisory analysis and planning; Python owns state."""

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from uuid import UUID

from sqlalchemy.orm import Session

from controller.app.artifacts import record_agent_context, save_json_artifact
from controller.app.executors.validation import check_path
from controller.app.integrations.langflow import LangflowClient, LangflowInvalidOutput, LangflowUnavailable
from controller.app.models import Run
from controller.app.repo_context import RepoContextError, load_agent_context, load_pinned_file_list
from controller.app.schemas import AnalysisResult, PlanPayload
from controller.app.services import get_run, record_plan
from controller.app.state_machine import ConflictError, event, transition


SessionFactory = Callable[[], Session]


@dataclass(frozen=True)
class ReasoningPolicy:
    repo_path: Path
    artifact_root: Path
    agent_context_sha256: str
    requirements_flow_id: str
    planning_flow_id: str
    review_flow_id: str = ""


def _flow_with_retry(
    client: LangflowClient, flow_id: str, input_data: dict, result_type: type,
    session_id: str, *, attempts: int, sleep: Callable[[float], None],
):
    for attempt in range(attempts):
        try:
            return client.run_json_flow(flow_id, input_data, result_type, session_id=session_id)
        except LangflowUnavailable:
            if attempt == attempts - 1:
                raise
            sleep(min(2 ** attempt, 4))
    raise AssertionError("unreachable")


def analyze_and_plan(
    session_factory: SessionFactory, run_id: UUID, policy: ReasoningPolicy,
    client: LangflowClient, *, max_attempts: int = 3,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    if not 1 <= max_attempts <= 3:
        raise ValueError("max attempts must be 1..3")
    with session_factory() as session:
        run = get_run(session, run_id)
        if run.status != "RECEIVED":
            raise ConflictError("analysis already started")
        transition(session, run, "ANALYZING")
        requirement = run.requirement
        base_commit = run.base_commit
        session.commit()

    stage = "analysis"
    try:
        context = load_agent_context(policy.repo_path, base_commit, policy.agent_context_sha256)
        file_list = load_pinned_file_list(policy.repo_path, base_commit)
        reasoning_context = f"{context.text}\n\nTracked files at the pinned commit:\n{file_list}"
        with session_factory() as session:
            record_agent_context(session, run_id, context)
            session.commit()
        analysis = _flow_with_retry(
            client, policy.requirements_flow_id,
            {"requirement": requirement, "repo_context": reasoning_context, "base_commit": base_commit},
            AnalysisResult, f"{run_id}:analysis", attempts=max_attempts, sleep=sleep,
        )
        with session_factory() as session:
            save_json_artifact(
                session, policy.artifact_root, run_id, "analysis", analysis.model_dump(mode="json")
            )
            run = get_run(session, run_id)
            if analysis.questions:
                run.failure_category = "AMBIGUOUS_SPEC"
                run.failure_stage = "analysis"
                run.failure_note = "; ".join(analysis.questions)[:500]
                transition(session, run, "NEEDS_HUMAN")
                event(session, run_id, "failure.classified", {
                    "category": "AMBIGUOUS_SPEC", "stage": "analysis",
                    "questions": analysis.questions,
                })
                session.commit()
                return
            transition(session, run, "PLANNING")
            session.commit()
        stage = "planning"
        plan = _flow_with_retry(
            client, policy.planning_flow_id,
            {
                "requirement": requirement, "analysis": analysis.model_dump(mode="json"),
                "repo_context": reasoning_context, "base_commit": base_commit,
            },
            PlanPayload, f"{run_id}:planning", attempts=max_attempts, sleep=sleep,
        )
        for task in plan.tasks:
            for path in task.allowed_paths:
                check_path(path, [path], policy.repo_path)
        with session_factory() as session:
            record_plan(session, run_id, plan)
            session.commit()
    except Exception as exc:
        category = "MISSING_CONTEXT" if isinstance(exc, RepoContextError) else (
            "INFRASTRUCTURE" if isinstance(exc, LangflowUnavailable)
            else "MODEL_LIMITATION" if isinstance(exc, LangflowInvalidOutput)
            else "OTHER"
        )
        with session_factory() as session:
            run = get_run(session, run_id)
            if run.status in {"ANALYZING", "PLANNING"}:
                run.failure_category = category
                run.failure_stage = stage
                run.failure_note = f"{type(exc).__name__}: {str(exc)[:400]}"
                transition(session, run, "FAILED")
                event(session, run_id, "failure.classified", {
                    "category": category, "stage": stage,
                })
                session.commit()
        raise
