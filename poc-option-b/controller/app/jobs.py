"""PostgreSQL-backed job queue on the executions table: enqueue, lease, reconcile."""

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from controller.app.events import record_event
from controller.app.models import Execution, Run, Task
from controller.app.state_machine import transition_run, transition_task
from controller.app.states import RUN_TRANSITIONS, TASK_TRANSITIONS, RunStatus, TaskStatus

LEASE = timedelta(seconds=120)
MAX_ATTEMPTS = 3
COORDINATOR = "delivery_coordinator"


def now() -> datetime:
    return datetime.now(timezone.utc)


def enqueue(session: Session, run: Run, kind: str, role: str, *, side_effects: bool, key: str,
            task: Task | None = None) -> Execution:
    """Queue work once per idempotency key; a repeated call returns the existing execution."""
    idempotency_key = f"{run.id}:{kind}:{key}"
    existing = session.scalar(select(Execution).where(Execution.idempotency_key == idempotency_key))
    if existing:
        return existing
    execution = Execution(run_id=run.id, task_id=task.id if task else None, kind=kind, role=role,
                          side_effects=side_effects, idempotency_key=idempotency_key, status="queued")
    session.add(execution)
    session.flush()
    record_event(session, "job.queued", actor=COORDINATOR, run_id=run.id,
                 payload={"kind": kind, "execution": str(execution.id), "task": task.external_task_id if task else None})
    return execution


def claim(session: Session, worker_id: str) -> Execution | None:
    execution = session.scalar(select(Execution).where(Execution.status == "queued")
                               .order_by(Execution.created_at).limit(1).with_for_update(skip_locked=True))
    if execution is None:
        return None
    execution.status = "running"
    execution.worker_id = worker_id
    execution.attempts += 1
    execution.started_at = now()
    execution.lease_until = now() + LEASE
    return execution


def heartbeat(session: Session, execution_id) -> None:
    execution = session.get(Execution, execution_id)
    if execution and execution.status == "running":
        execution.lease_until = now() + LEASE


def finish(session: Session, execution: Execution, status: str, *, result: dict | None = None,
           error: str | None = None) -> None:
    execution.status = status
    execution.result = result
    execution.error = error
    execution.finished_at = now()
    execution.lease_until = None


def reconcile_expired(session: Session) -> list[Execution]:
    """Handle executions whose worker stopped renewing the lease.

    Reasoning work has no side effects and is queued again. Work with side effects
    (code, pushes, pull requests) is never replayed: it becomes UNKNOWN and a human decides.
    """
    expired = session.scalars(select(Execution).where(Execution.status == "running", Execution.lease_until < now())
                              .with_for_update(skip_locked=True)).all()
    for execution in expired:
        run = session.get(Run, execution.run_id)
        if not execution.side_effects and execution.attempts < MAX_ATTEMPTS:
            execution.status, execution.worker_id, execution.lease_until = "queued", None, None
            record_event(session, "job.requeued", actor=COORDINATOR, run_id=run.id,
                         payload={"kind": execution.kind, "execution": str(execution.id), "reason": "lease expired"})
            continue
        finish(session, execution, "unknown", error="lease expired; outcome unknown")
        if execution.task_id:
            task = session.get(Task, execution.task_id)
            if TaskStatus.UNKNOWN in TASK_TRANSITIONS[TaskStatus(task.status)]:
                transition_task(session, task, TaskStatus.UNKNOWN, actor=COORDINATOR, reason="lease expired")
        if RunStatus.NEEDS_HUMAN in RUN_TRANSITIONS[RunStatus(run.status)]:
            transition_run(session, run, RunStatus.NEEDS_HUMAN, actor=COORDINATOR, resume_status=run.status,
                           reason=f"{execution.kind} lost its worker; inspect the worktree before resuming")
    return expired
