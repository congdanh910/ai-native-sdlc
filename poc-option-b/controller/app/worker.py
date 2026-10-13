"""Worker process: claims queued executions and lets the coordinator handle them.

Run with `python -m controller.app.worker`; `sdlc up` starts it in the background.
"""

import logging
import os
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time

from sqlalchemy import select

from controller.app import coordinator, jobs
from controller.app.db import session_scope
from controller.app.models import Execution, Run
from controller.app.states import RUN_TRANSITIONS, RunStatus
from controller.app.team import get_team
from controller.app.integrations.langflow import InvalidModelOutput
from controller.app.team.base import CapacityBusy, Team

log = logging.getLogger("sdlc.worker")
WORKER_ID = f"{socket.gethostname()}:{os.getpid()}"
CAPACITY_WAIT_SECONDS = 120
RETRY_BASE_SECONDS = 10
PR_POLL_SECONDS = 60  # without a public webhook, GitHub is polled for CI, review and merge


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
        if isinstance(exc, CapacityBusy):
            jobs.postpone(session, execution, CAPACITY_WAIT_SECONDS, message)
            return
        retryable = not execution.side_effects and not isinstance(exc, InvalidModelOutput)
        if retryable and execution.attempts < jobs.MAX_ATTEMPTS:
            jobs.postpone(session, execution, RETRY_BASE_SECONDS * 2 ** execution.attempts, message)
            execution.attempts += 1  # postpone does not count capacity waits; failures do count
            return
        jobs.finish(session, execution, "failed", error=message)
        if RunStatus.NEEDS_HUMAN in RUN_TRANSITIONS[RunStatus(run.status)]:
            coordinator.to_needs_human(session, run, f"{execution.kind} failed: {message}")


def advance_evals() -> None:
    from controller.app import evals
    with session_scope() as session:
        for eval_id in evals.advance(session):
            log.info("eval %s completed", eval_id)


class StayAwake:
    """Keep macOS from idle-sleeping while the team has work; sleep would cut the GX10 tunnel."""

    def __init__(self, idle_release_seconds: float = 120):
        self.idle_release_seconds = idle_release_seconds
        self.process: subprocess.Popen | None = None
        self.last_work = 0.0

    def busy(self) -> None:
        self.last_work = time.monotonic()
        if self.process is None and sys.platform == "darwin" and shutil.which("caffeinate"):
            self.process = subprocess.Popen(["caffeinate", "-i", "-w", str(os.getpid())])
            log.info("holding an idle-sleep assertion while jobs run")

    def idle(self, queue_has_work: bool) -> None:
        if queue_has_work:
            self.busy()
        elif self.process and time.monotonic() - self.last_work > self.idle_release_seconds:
            self.release()

    def release(self) -> None:
        if self.process:
            self.process.terminate()
            self.process = None
            log.info("released the idle-sleep assertion")


def _queue_has_work() -> bool:
    with session_scope() as session:
        return session.scalar(select(Execution.id).where(Execution.status.in_(("queued", "running"))).limit(1)) is not None


def run_forever(poll_seconds: float = 1.0) -> None:
    team = get_team()
    stopping = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stopping.set())
    awake = StayAwake()
    log.info("worker %s started with team %s", WORKER_ID, team.engine)
    next_pr_poll = 0.0
    while not stopping.is_set():
        try:
            busy = run_once(team)
            if busy:
                awake.busy()
            else:
                awake.idle(_queue_has_work())
        except Exception:
            log.exception("worker loop error")
            busy = False
        if not busy:
            try:
                advance_evals()
            except Exception:
                log.exception("eval scoring failed")
            if time.monotonic() >= next_pr_poll:
                next_pr_poll = time.monotonic() + PR_POLL_SECONDS
                try:
                    with session_scope() as session:
                        coordinator.poll_pull_requests(session)
                except Exception:
                    log.exception("pull request polling failed")
            stopping.wait(poll_seconds)
    awake.release()
    log.info("worker stopped")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    run_forever()
