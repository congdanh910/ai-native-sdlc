from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import update
from sqlalchemy.orm import Session

from controller.app.models import Event, Run


TRANSITIONS: dict[str, frozenset[str]] = {
    "RECEIVED": frozenset({"ANALYZING", "CANCELLED"}),
    "ANALYZING": frozenset({"PLANNING", "FAILED", "NEEDS_HUMAN", "CANCELLED"}),
    "PLANNING": frozenset({"WAITING_PLAN_APPROVAL", "FAILED", "NEEDS_HUMAN", "CANCELLED"}),
    "WAITING_PLAN_APPROVAL": frozenset({"READY", "NEEDS_HUMAN", "CANCELLED"}),
    "READY": frozenset({"CODING", "CANCELLED"}),
    "CODING": frozenset({"VALIDATING", "CANCELLING", "FAILED", "NEEDS_HUMAN"}),
    "CANCELLING": frozenset({"CANCELLED", "NEEDS_HUMAN"}),
    "VALIDATING": frozenset({"REVIEWING", "FAILED", "NEEDS_HUMAN"}),
    "REVIEWING": frozenset({"PR_READY", "FAILED", "NEEDS_HUMAN"}),
    "PR_READY": frozenset({"WAITING_PR_APPROVAL", "NEEDS_HUMAN", "CANCELLED"}),
    "WAITING_PR_APPROVAL": frozenset({"COMPLETED", "NEEDS_HUMAN", "CANCELLED"}),
    "NEEDS_HUMAN": frozenset({"CANCELLED"}),
    "FAILED": frozenset({"VALIDATING"}),
    "CANCELLED": frozenset(),
    "COMPLETED": frozenset(),
}


class ConflictError(Exception):
    pass


class NotFoundError(Exception):
    pass


class CancellationRequested(Exception):
    pass


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def event(session: Session, run_id, event_type: str, payload: dict) -> None:
    session.add(Event(id=uuid4(), run_id=run_id, event_type=event_type, payload_jsonb=payload))


def transition(session: Session, run: Run, target: str, expected_version: int | None = None) -> Run:
    if target not in TRANSITIONS.get(run.status, frozenset()):
        raise ConflictError(f"invalid transition: {run.status} -> {target}")
    if expected_version is not None and run.version != expected_version:
        raise ConflictError("stale run version")
    statement = (
        update(Run)
        .where(Run.id == run.id, Run.status == run.status, Run.version == run.version)
        .values(status=target, version=Run.version + 1, updated_at=utc_now())
        .execution_options(synchronize_session=False)
    )
    if session.execute(statement).rowcount != 1:
        raise ConflictError("run changed concurrently")
    session.expire(run)
    session.refresh(run)
    event(session, run.id, "run.transition", {"status": target, "version": run.version})
    return run
