from datetime import timedelta
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from controller.app.models import Execution, Run, Task, TaskDependency
from controller.app.services import get_run
from controller.app.state_machine import CancellationRequested, ConflictError, event, transition, utc_now


def claim_next_execution(
    session: Session, worker_id: str, lease_seconds: int = 60,
    executor_name: str | None = None, run_id: UUID | None = None,
) -> Execution | None:
    query = select(Execution).join(Task, Execution.task_id == Task.id).where(Execution.status == "QUEUED")
    if run_id is not None:
        query = query.where(Task.run_id == run_id)
    if executor_name is not None:
        if executor_name not in {"codex", "claude"}:
            raise ValueError("unsupported executor")
        query = query.where(Task.executor == executor_name)
    execution = None
    task = None
    run = None
    for candidate in session.scalars(
        query.order_by(Execution.created_at, Execution.id)
        .with_for_update(of=Execution, skip_locked=True)
    ):
        candidate_task = session.get(Task, candidate.task_id)
        if candidate_task is None:
            raise ConflictError("queued execution has no task")
        # One integration worktree is shared by a run. Lock its row before
        # checking active work so two workers cannot code the same run at once.
        candidate_run = session.scalar(
            select(Run).where(Run.id == candidate_task.run_id).with_for_update()
        )
        active = session.scalar(
            select(Execution.id).join(Task, Execution.task_id == Task.id)
            .where(Task.run_id == candidate_run.id, Execution.status == "RUNNING").limit(1)
        )
        if active is None:
            execution, task, run = candidate, candidate_task, candidate_run
            break
    if execution is None or task is None or run is None:
        return None
    if run.status not in {"READY", "CODING"}:
        raise ConflictError("queued execution belongs to a run that cannot code")
    if run.status == "READY":
        transition(session, run, "CODING")
    execution.status = "RUNNING"
    execution.worker_id = worker_id
    execution.lease_until = utc_now() + timedelta(seconds=lease_seconds)
    task.status = "RUNNING"
    task.attempt_count += 1
    event(session, run.id, "execution.claimed", {"execution_id": str(execution.id), "worker_id": worker_id})
    return execution


def heartbeat_execution(session: Session, execution_id: UUID, worker_id: str, lease_seconds: int = 60) -> None:
    execution = session.get(Execution, execution_id)
    if execution is None or execution.status != "RUNNING" or execution.worker_id != worker_id:
        raise ConflictError("execution is not owned by this worker")
    if execution.lease_until is None or execution.lease_until.replace(tzinfo=None) < utc_now().replace(tzinfo=None):
        raise ConflictError("execution lease expired")
    task = session.get(Task, execution.task_id)
    if get_run(session, task.run_id).status == "CANCELLING":
        raise CancellationRequested("run cancellation requested")
    execution.lease_until = utc_now() + timedelta(seconds=lease_seconds)


def _owned_execution(session: Session, execution_id: UUID, worker_id: str) -> Execution:
    execution = session.get(Execution, execution_id)
    if execution is None or execution.status != "RUNNING" or execution.worker_id != worker_id:
        raise ConflictError("execution is not owned by this worker")
    if execution.lease_until is None or execution.lease_until.replace(tzinfo=None) < utc_now().replace(tzinfo=None):
        raise ConflictError("execution lease expired")
    return execution


def record_workspace(session: Session, execution_id: UUID, worker_id: str, path: str, branch: str) -> None:
    execution = _owned_execution(session, execution_id, worker_id)
    if execution.worktree_path is not None:
        raise ConflictError("execution already has a worktree")
    execution.worktree_path = path
    execution.branch = branch
    task = session.get(Task, execution.task_id)
    event(session, task.run_id, "execution.workspace_created", {
        "execution_id": str(execution.id), "worktree_path": path, "branch": branch,
    })


def record_process(session: Session, execution_id: UUID, worker_id: str, pid: int, pgid: int) -> None:
    execution = _owned_execution(session, execution_id, worker_id)
    if pid <= 0 or pgid <= 0:
        raise ConflictError("execution process identity is invalid")
    # Keep the latest child here; each start is also retained in the audit log.
    execution.process_id = pid
    execution.process_group_id = pgid
    task = session.get(Task, execution.task_id)
    event(session, task.run_id, "execution.process_started", {
        "execution_id": str(execution.id), "process_id": pid, "process_group_id": pgid,
    })


def record_remediation_attempt(
    session: Session, execution_id: UUID, worker_id: str, attempt: int,
) -> None:
    execution = _owned_execution(session, execution_id, worker_id)
    if attempt not in {1, 2}:
        raise ValueError("remediation attempt must be 1 or 2")
    task = session.get(Task, execution.task_id)
    event(session, task.run_id, "execution.remediation_started", {
        "execution_id": str(execution.id), "attempt": attempt,
    })


def complete_execution(session: Session, execution_id: UUID, worker_id: str, result: dict) -> None:
    execution = _owned_execution(session, execution_id, worker_id)
    task = session.get(Task, execution.task_id)
    run = get_run(session, task.run_id)
    if run.status == "CANCELLING":
        raise CancellationRequested("run cancellation requested")
    execution.status = "COMPLETED"
    execution.result_jsonb = result
    execution.lease_until = None
    task.status = "DONE"
    event(session, run.id, "execution.completed", {"execution_id": str(execution.id)})
    session.flush()
    blocked_tasks = session.scalars(select(Task).where(Task.run_id == run.id, Task.status == "BLOCKED")).all()
    for candidate in blocked_tasks:
        parent_ids = session.scalars(
            select(TaskDependency.depends_on_task_id).where(TaskDependency.task_id == candidate.id)
        ).all()
        if all(session.get(Task, parent_id).status == "DONE" for parent_id in parent_ids):
            candidate.status = "QUEUED"
            session.add(
                Execution(
                    id=uuid4(), task_id=candidate.id,
                    idempotency_key=f"{candidate.id}:0", status="QUEUED",
                )
            )
    session.flush()
    if run.status == "CODING" and all(
        item.status == "DONE" for item in session.scalars(select(Task).where(Task.run_id == run.id))
    ):
        transition(session, run, "VALIDATING")


def complete_mock_execution(session: Session, execution_id: UUID, worker_id: str) -> None:
    """B2-only completion hook for deterministic tests; it does not launch a CLI."""
    complete_execution(session, execution_id, worker_id, {"mock": True})


def fail_execution(
    session: Session, execution_id: UUID, worker_id: str, *,
    category: str, stage: str, note: str,
) -> None:
    execution = _owned_execution(session, execution_id, worker_id)
    task = session.get(Task, execution.task_id)
    run = get_run(session, task.run_id)
    execution.status = "FAILED"
    execution.lease_until = None
    execution.result_jsonb = {"failure_category": category, "failure_stage": stage, "failure_note": note[:500]}
    task.status = "NEEDS_HUMAN"
    run.failure_category = category
    run.failure_stage = stage
    run.failure_note = note[:500]
    if run.status == "CODING":
        transition(session, run, "NEEDS_HUMAN")
    event(session, run.id, "execution.failed", {
        "execution_id": str(execution.id), "category": category, "stage": stage,
    })


def cancel_execution(session: Session, execution_id: UUID, worker_id: str) -> None:
    """Call only after the owned child process has stopped."""
    execution = _owned_execution(session, execution_id, worker_id)
    task = session.get(Task, execution.task_id)
    run = get_run(session, task.run_id)
    if run.status != "CANCELLING":
        raise ConflictError("run is not cancelling")
    execution.status = "CANCELLED"
    execution.lease_until = None
    execution.result_jsonb = {"cancelled": True}
    task.status = "CANCELLED"
    event(session, run.id, "execution.cancelled", {"execution_id": str(execution.id)})
    session.flush()
    active = session.scalar(
        select(Execution.id)
        .join(Task, Execution.task_id == Task.id)
        .where(Task.run_id == run.id, Execution.status == "RUNNING")
        .limit(1)
    )
    if active is None:
        transition(session, run, "CANCELLED")


def reconcile_expired_executions(session: Session) -> list[UUID]:
    expired = session.scalars(
        select(Execution)
        .where(Execution.status == "RUNNING", Execution.lease_until < utc_now())
        .with_for_update(skip_locked=True)
    ).all()
    ids: list[UUID] = []
    for execution in expired:
        task = session.get(Task, execution.task_id)
        run = get_run(session, task.run_id)
        execution.status = "UNKNOWN"
        task.status = "NEEDS_HUMAN"
        if run.status in {"CODING", "CANCELLING"}:
            run.failure_category = "INFRASTRUCTURE"
            run.failure_stage = "coding"
            run.failure_note = "Coding worker lease expired; execution outcome requires human review"
            transition(session, run, "NEEDS_HUMAN")
            event(session, run.id, "failure.classified", {
                "category": "INFRASTRUCTURE", "stage": "coding",
            })
        event(session, run.id, "execution.lease_expired", {"execution_id": str(execution.id)})
        ids.append(execution.id)
    return ids
