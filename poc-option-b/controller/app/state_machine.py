"""Explicit, transactional, version-checked state transitions."""

from sqlalchemy import update
from sqlalchemy.orm import Session

from controller.app.events import record_event
from controller.app.models import Run, Task
from controller.app.states import RUN_TRANSITIONS, TASK_TRANSITIONS, RunStatus, TaskStatus


class InvalidTransition(Exception):
    pass


class StaleVersion(Exception):
    pass


def transition_run(session: Session, run: Run, to: RunStatus, *, actor: str, acting_role: str | None = None,
                   reason: str | None = None, expected_version: int | None = None, **changes) -> Run:
    """Move a run to `to`. The UPDATE only applies if the version is unchanged since it was read."""
    current = RunStatus(run.status)
    if to not in RUN_TRANSITIONS[current]:
        raise InvalidTransition(f"run cannot go from {current} to {to}")
    version = run.version if expected_version is None else expected_version
    if version != run.version:
        raise StaleVersion(f"run is at version {run.version}, not {version}")
    values = {"status": to.value, "version": version + 1, "status_reason": reason, **changes}
    result = session.execute(update(Run).where(Run.id == run.id, Run.version == version).values(**values)
                             .execution_options(synchronize_session=False))
    if result.rowcount != 1:
        raise StaleVersion("run changed concurrently; reload and retry")
    for key, value in values.items():
        setattr(run, key, value)
    record_event(session, "run.transition", actor=actor, acting_role=acting_role, run_id=run.id,
                 project_id=run.project_id,
                 payload={"from": current.value, "to": to.value, "version": run.version, "reason": reason})
    return run


def transition_task(session: Session, task: Task, to: TaskStatus, *, actor: str, reason: str | None = None,
                    **changes) -> Task:
    current = TaskStatus(task.status)
    if to not in TASK_TRANSITIONS[current]:
        raise InvalidTransition(f"task {task.external_task_id} cannot go from {current} to {to}")
    values = {"status": to.value, "version": task.version + 1, **changes}
    result = session.execute(update(Task).where(Task.id == task.id, Task.version == task.version).values(**values)
                             .execution_options(synchronize_session=False))
    if result.rowcount != 1:
        raise StaleVersion("task changed concurrently; reload and retry")
    for key, value in values.items():
        setattr(task, key, value)
    record_event(session, "task.transition", actor=actor, run_id=task.run_id,
                 payload={"task": task.external_task_id, "from": current.value, "to": to.value, "reason": reason})
    return task
