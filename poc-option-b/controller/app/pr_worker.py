"""Prepare an evidence-backed draft PR for a completed pilot run."""

import hashlib
import json
import os
from pathlib import Path
from typing import Callable
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from controller.app.executors.process import run_process
from controller.app.models import Artifact, Event, Execution, Plan, Task
from controller.app.review_worker import _worktree_diff
from controller.app.schemas import PlanTask
from controller.app.services import get_run, record_draft_pr
from controller.app.state_machine import ConflictError


SessionFactory = Callable[[], Session]


async def publish_draft_pr(
    session_factory: SessionFactory, run_id: UUID, *, repo_full_name: str,
    baseline_branch: str, worktree_root: Path, artifact_root: Path,
) -> str:
    """Call only after review passed; never retries external writes automatically."""
    with session_factory() as session:
        run = get_run(session, run_id)
        if run.status != "PR_READY" or run.repo_id != repo_full_name:
            raise ConflictError("run is not ready for a draft PR")
        tasks = session.scalars(select(Task).where(Task.run_id == run_id)).all()
        if not tasks or any(task.status != "DONE" for task in tasks):
            raise ConflictError("pilot draft PR requires all tasks completed")
        plan_ids = {task.plan_id for task in tasks}
        if len(plan_ids) != 1:
            raise ConflictError("tasks do not share one approved plan")
        plan = session.get(Plan, next(iter(plan_ids)))
        completed_events = session.scalars(
            select(Event).where(Event.run_id == run_id, Event.event_type == "execution.completed")
            .order_by(Event.created_at, Event.id)
        ).all()
        if len(completed_events) != len(tasks):
            raise ConflictError("execution history is incomplete")
        evidence = []
        task_by_id = {task.id: task for task in tasks}
        for item in completed_events:
            execution = session.get(Execution, UUID(item.payload_jsonb["execution_id"]))
            if (execution is None or execution.status != "COMPLETED"
                    or execution.task_id not in task_by_id or not execution.result_jsonb):
                raise ConflictError("completed execution evidence is incomplete")
            evidence.append((task_by_id[execution.task_id], execution))
        by_base = {}
        for item in evidence:
            item_base = item[1].result_jsonb.get("task_base_commit")
            if not item_base or item_base in by_base:
                raise ConflictError("task integration history is ambiguous")
            by_base[item_base] = item
        evidence = []
        current = run.base_commit
        while item := by_base.pop(current, None):
            evidence.append(item)
            current = item[1].result_jsonb.get("integration_commit")
        if by_base or len(evidence) != len(tasks):
            raise ConflictError("task integration history is discontinuous")
        review = session.scalar(select(Artifact).where(
            Artifact.run_id == run_id, Artifact.kind == "review",
        ).order_by(Artifact.created_at.desc()).limit(1))
        if not review:
            raise ConflictError("draft PR evidence is incomplete")
        branches = {execution.branch for _, execution in evidence}
        paths = {execution.worktree_path for _, execution in evidence}
        if len(branches) != 1 or len(paths) != 1 or None in branches or None in paths:
            raise ConflictError("task worktrees do not form one integration branch")
        branch = next(iter(branches))
        worktree_path = next(iter(paths))
        task_evidence = [(task.payload_jsonb, execution.result_jsonb) for task, execution in evidence]
        requirement = run.requirement
        base_commit = run.base_commit
        executor = run.executor
        plan_sha256 = plan.sha256
    root = worktree_root.resolve(strict=True)
    worktree = Path(worktree_path).resolve(strict=True)
    if not worktree.is_relative_to(root):
        raise ConflictError("execution worktree is outside the configured root")
    review_path = Path(review.storage_path).resolve(strict=True)
    if not review_path.is_relative_to(artifact_root.resolve(strict=True)):
        raise ConflictError("review artifact is outside the configured root")
    review_data = review_path.read_bytes()
    if hashlib.sha256(review_data).hexdigest() != review.checksum:
        raise ConflictError("review artifact checksum mismatch")
    if json.loads(review_data).get("verdict") != "pass":
        raise ConflictError("advisory review did not pass")
    current_commit = base_commit
    changed: set[str] = set()
    validation: list[tuple[str, dict]] = []
    for payload, result in task_evidence:
        task_plan = PlanTask.model_validate(payload)
        if result.get("task_base_commit") != current_commit:
            raise ConflictError("task integration history is discontinuous")
        await _worktree_diff(
            worktree, current_commit, result["integration_commit"],
            task_plan.allowed_paths, result["changed_paths"],
        )
        checks = result.get("validation", [])
        if not checks or any(item.get("exit_code") != 0 for item in checks):
            raise ConflictError("validation evidence is missing or failed")
        changed.update(result["changed_paths"])
        validation.extend((task_plan.id, item) for item in checks)
        current_commit = result["integration_commit"]
    head = await run_process(["git", "rev-parse", "HEAD"], worktree, timeout_seconds=15)
    if head.returncode or head.stdout.strip() != current_commit:
        raise ConflictError("integration branch HEAD differs from validated evidence")
    remote = await run_process(["git", "remote", "get-url", "origin"], worktree, timeout_seconds=15)
    if remote.returncode or remote.stdout.strip() != f"https://github.com/{repo_full_name}.git":
        raise ConflictError("worktree remote does not match the allowlisted pilot repository")
    report = (
        "## SDLC execution evidence\n\n"
        f"Run: `{run_id}`  \nBase commit: `{base_commit}`  \nExecutor: `{executor}`  "
        f"\nPlan checksum: `{plan_sha256}`\n\n"
        f"Requirement:\n\n{requirement[:4000]}\n\n"
        "Changed paths:\n" + "".join(f"- `{path}`\n" for path in sorted(changed)) + "\n"
        "Validation:\n" + "".join(
            f"- {task_id}: `{item['command']}`: exit {item['exit_code']}\n"
            for task_id, item in validation
        ) + "\nAdvisory review: pass. Human approval is required before merge.\n"
    )
    report_dir = artifact_root.resolve() / str(run_id)
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / f"pr-report-{uuid4().hex}.md"
    with report_path.open("x", encoding="utf-8") as stream:
        os.fchmod(stream.fileno(), 0o600)
        stream.write(report)
    with session_factory() as session:
        session.add(Artifact(
            id=uuid4(), run_id=run_id, kind="pr_report",
            storage_path=str(report_path), checksum=hashlib.sha256(report.encode()).hexdigest(),
        ))
        session.commit()

    async def checked(argv: list[str], timeout: float = 60) -> str:
        result = await run_process(argv, worktree, timeout_seconds=timeout)
        if result.returncode or result.stdout_truncated or result.stderr_truncated:
            raise RuntimeError(f"external PR command failed: {argv[0]} {argv[1]}")
        return result.stdout.strip()

    head_sha = current_commit
    title = requirement.splitlines()[0].lstrip("# ").strip()[:100]
    if not title:
        raise ConflictError("pilot requirement has no PR title")
    await checked([
        "git", "-c", "core.hooksPath=/dev/null", "push", "origin",
        f"HEAD:refs/heads/{branch}",
    ], timeout=120)
    await checked([
        "gh", "pr", "create", "--draft", "--repo", repo_full_name,
        "--base", baseline_branch, "--head", branch,
        "--title", f"[SDLC] {title}", "--body-file", str(report_path),
    ], timeout=120)
    details = json.loads(await checked([
        "gh", "pr", "view", branch, "--repo", repo_full_name,
        "--json", "number,url,headRefOid,headRefName,baseRefName,isDraft",
    ], timeout=30))
    if (details.get("headRefOid") != head_sha
            or details.get("headRefName") != branch
            or details.get("baseRefName") != baseline_branch
            or details.get("isDraft") is not True):
        raise RuntimeError("created PR identity could not be verified")
    with session_factory() as session:
        record_draft_pr(
            session, run_id, repo_full_name=repo_full_name,
            number=details["number"], url=details["url"], head_sha=head_sha,
        )
        session.commit()
    return details["url"]
