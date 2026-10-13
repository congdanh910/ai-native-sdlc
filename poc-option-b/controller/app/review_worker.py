"""Build review evidence from completed worktrees and request advisory review."""

import asyncio
import time
from pathlib import Path
from typing import Callable
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from controller.app.artifacts import save_json_artifact
from controller.app.executors.process import run_process
from controller.app.executors.validation import check_path
from controller.app.integrations.langflow import LangflowClient, LangflowInvalidOutput, LangflowUnavailable
from controller.app.models import Execution, Plan, Task
from controller.app.reasoning_worker import ReasoningPolicy, _flow_with_retry
from controller.app.repo_context import RepoContextError, load_agent_context
from controller.app.schemas import PlanTask, ReviewResult
from controller.app.services import get_run
from controller.app.state_machine import ConflictError, event, transition


MAX_DIFF_CHARS = 200_000


async def _worktree_diff(
    worktree: Path, task_base_commit: str, integration_commit: str,
    allowed_paths: list[str], expected_paths: list[str],
) -> str:
    clean = await run_process(["git", "status", "--porcelain", "-z"], worktree, timeout_seconds=15)
    ancestor = await run_process(
        ["git", "merge-base", "--is-ancestor", integration_commit, "HEAD"],
        worktree, timeout_seconds=15,
    )
    if clean.returncode or clean.stdout or ancestor.returncode:
        raise ValueError("review worktree is not clean")
    parent = await run_process(
        ["git", "rev-parse", f"{integration_commit}^"], worktree, timeout_seconds=15,
    )
    if parent.returncode or parent.stdout.strip() != task_base_commit:
        raise ValueError("task integration commit does not follow its validated base")
    names = await run_process(
        ["git", "diff", "--name-only", "-z", task_base_commit, integration_commit, "--"],
        worktree, timeout_seconds=15,
    )
    if names.returncode or names.stdout_truncated:
        raise ValueError("task changed paths are unavailable")
    paths = sorted(path for path in names.stdout.split("\x00") if path)
    if not paths or paths != expected_paths:
        raise ValueError("committed task paths differ from validation evidence")
    for path in paths:
        check_path(path, allowed_paths, worktree)
    tracked = await run_process(
        ["git", "diff", "--no-ext-diff", "--no-textconv", "--no-color",
         task_base_commit, integration_commit, "--"],
        worktree, timeout_seconds=30,
    )
    if tracked.returncode or tracked.stdout_truncated:
        raise ValueError("tracked diff is unavailable or too large")
    result = tracked.stdout
    if len(result) > MAX_DIFF_CHARS:
        raise ValueError("combined diff exceeds review limit")
    return result


async def review_run(
    session_factory: Callable[[], Session], run_id: UUID, policy: ReasoningPolicy,
    worktree_root: Path, client: LangflowClient, *, max_attempts: int = 3,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    if not 1 <= max_attempts <= 3:
        raise ValueError("max attempts must be 1..3")
    with session_factory() as session:
        run = get_run(session, run_id)
        if run.status != "VALIDATING":
            raise ConflictError("run is not ready for advisory review")
        tasks = session.scalars(select(Task).where(Task.run_id == run_id)).all()
        if not tasks or any(task.status != "DONE" for task in tasks):
            raise ConflictError("not all coding tasks are complete")
        plan = session.scalar(select(Plan).where(Plan.run_id == run_id).order_by(Plan.version.desc()).limit(1))
        if plan is None:
            raise ConflictError("approved plan is missing")
        evidence = []
        for task in tasks:
            execution = session.scalar(
                select(Execution).where(Execution.task_id == task.id, Execution.status == "COMPLETED")
                .order_by(Execution.created_at.desc()).limit(1)
            )
            if execution is None or execution.worktree_path is None or execution.result_jsonb is None:
                raise ConflictError("completed task is missing execution evidence")
            evidence.append((task.payload_jsonb, execution.worktree_path, execution.result_jsonb))
        requirement = run.requirement
        base_commit = run.base_commit
        plan_body = plan.body
        transition(session, run, "REVIEWING")
        session.commit()

    try:
        context = load_agent_context(policy.repo_path, base_commit, policy.agent_context_sha256)
        root = worktree_root.resolve(strict=True)
        diffs = []
        test_evidence = []
        for payload, worktree_path, result in evidence:
            task = PlanTask.model_validate(payload)
            worktree = Path(worktree_path).resolve(strict=True)
            if not worktree.is_relative_to(root):
                raise ValueError("execution worktree is outside the configured root")
            task_diff = await _worktree_diff(
                worktree, result["task_base_commit"], result["integration_commit"],
                task.allowed_paths, result["changed_paths"],
            )
            diffs.append(f"Task {task.id}:\n{task_diff}")
            test_evidence.append({
                "task_id": task.id, "changed_paths": result.get("changed_paths", []),
                "validation": result.get("validation", []),
            })
        review = await asyncio.to_thread(
            _flow_with_retry, client, policy.review_flow_id,
            {
                "requirement": requirement, "plan": plan_body,
                "diff": "\n\n".join(diffs), "test_evidence": test_evidence,
                "repo_context": context.text,
            },
            ReviewResult, f"{run_id}:review", attempts=max_attempts, sleep=sleep,
        )
        with session_factory() as session:
            save_json_artifact(
                session, policy.artifact_root, run_id, "review", review.model_dump(mode="json")
            )
            run = get_run(session, run_id)
            if review.verdict == "pass":
                transition(session, run, "PR_READY")
            else:
                run.failure_category = "MODEL_LIMITATION"
                run.failure_stage = "review"
                run.failure_note = "; ".join(finding.message for finding in review.findings)[:500]
                transition(session, run, "NEEDS_HUMAN")
                event(session, run_id, "review.needs_changes", {"findings": len(review.findings)})
                event(session, run_id, "failure.classified", {
                    "category": "MODEL_LIMITATION", "stage": "review",
                })
            session.commit()
    except Exception as exc:
        category = "MISSING_CONTEXT" if isinstance(exc, RepoContextError) else (
            "INFRASTRUCTURE" if isinstance(exc, LangflowUnavailable)
            else "MODEL_LIMITATION" if isinstance(exc, LangflowInvalidOutput)
            else "OTHER"
        )
        with session_factory() as session:
            run = get_run(session, run_id)
            if run.status == "REVIEWING":
                run.failure_category = category
                run.failure_stage = "review"
                run.failure_note = f"{type(exc).__name__}: {str(exc)[:400]}"
                transition(session, run, "FAILED")
                event(session, run_id, "failure.classified", {"category": category, "stage": "review"})
                session.commit()
        raise
