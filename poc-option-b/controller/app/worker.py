"""Worker process: claims queued executions and lets the coordinator handle them.

Run with `python -m controller.app.worker`; `sdlc up` starts it in the background.
"""

import logging
import os
import signal
import socket
import threading
import time

from controller.app import coordinator, jobs
from controller.app.db import session_scope
from controller.app.models import Execution, Run
from controller.app.states import RUN_TRANSITIONS, RunStatus
from controller.app.team import get_team
from controller.app.team.base import Team

log = logging.getLogger("sdlc.worker")
WORKER_ID = f"{socket.gethostname()}:{os.getpid()}"


def _heartbeat(execution_id, stop: threading.Event) -> None:
    while not stop.wait(jobs.LEASE.total_seconds() / 3):
        with session_scope() as session:
            jobs.heartbeat(session, execution_id)


def run_once(team: Team, worker_id: str = WORKER_ID) -> bool:
    """Handle at most one job. Returns False when the queue is empty."""
    with session_scope() as session:
        jobs.reconcile_expired(session)
    with session_scope() as session:
        execution = jobs.claim(session, worker_id)
        if execution is None:
            return False
        coordinator.on_claim(session, execution)
        execution_id, kind = execution.id, execution.kind
    log.info("claimed %s %s", kind, execution_id)
    stop = threading.Event()
    beat = threading.Thread(target=_heartbeat, args=(execution_id, stop), daemon=True)
    beat.start()
    try:
        with session_scope() as session:
            loaded = coordinator.load(session, session.get(Execution, execution_id))
        result = coordinator.compute(team, loaded)
        with session_scope() as session:
            coordinator.apply(session, loaded, result, team)
    except Exception as exc:  # the run must never be left silently stuck
        log.exception("job %s failed", execution_id)
        _fail(execution_id, exc)
    finally:
        stop.set()
    return True


def _fail(execution_id, exc: Exception) -> None:
    with session_scope() as session:
        execution = session.get(Execution, execution_id, with_for_update=True)
        run = session.get(Run, execution.run_id, with_for_update=True)
        message = f"{type(exc).__name__}: {exc}"[:500]
        if not execution.side_effects and execution.attempts < jobs.MAX_ATTEMPTS:
            execution.status, execution.worker_id, execution.lease_until, execution.error = "queued", None, None, message
            return
        jobs.finish(session, execution, "failed", error=message)
        if RunStatus.NEEDS_HUMAN in RUN_TRANSITIONS[RunStatus(run.status)]:
            coordinator.to_needs_human(session, run, f"{execution.kind} failed: {message}")


def run_forever(poll_seconds: float = 1.0) -> None:
    team = get_team()
    stopping = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stopping.set())
    log.info("worker %s started with team %s", WORKER_ID, team.engine)
    while not stopping.is_set():
        try:
            busy = run_once(team)
        except Exception:
            log.exception("worker loop error")
            busy = False
        if not busy:
            stopping.wait(poll_seconds)
    log.info("worker stopped")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    run_forever()
