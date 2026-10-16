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

from controller.app import coordinator, joblog, jobs
from controller.app.config import get_settings
from controller.app.doing import process
from controller.app.db import session_scope
from controller.app.models import Execution, Run
from controller.app.states import RUN_TRANSITIONS, RunStatus
from controller.app.team import get_team
from controller.app.integrations.langflow import InvalidModelOutput
from controller.app.team.base import CapacityBusy, Team

log = logging.getLogger("sdlc.worker")
WORKER_ID = f"{socket.gethostname()}:{os.getpid()}"




def _heartbeat(execution_id, stop: threading.Event) -> None:
    """Renew the lease, and stop the job's sessions and containers as soon as its run is cancelled."""
    settings = get_settings()
    renew_every = max(1, int(settings.job_lease_seconds / 3 / settings.cancel_check_seconds))
    ticks = 0
    while not stop.wait(settings.cancel_check_seconds):
        ticks += 1
        with session_scope() as session:
            execution = session.get(Execution, execution_id)
            run = session.get(Run, execution.run_id) if execution else None
            if run is not None and run.status == RunStatus.CANCELLED:
                stopped = process.cancel_active()
                if stopped:
                    log.info("run %s cancelled: stopped %d running process(es)", run.id, stopped)
            if ticks % renew_every == 0:
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
        execution_id, kind, run_id = execution.id, execution.kind, execution.run_id
        joblog.start(run_id, execution_id, f"{kind} ({execution.role}) claimed by {worker_id}, attempt {execution.attempts}")  # claim counted it
    log.info("claimed %s %s", kind, execution_id)
    process.begin_job()
    stop = threading.Event()
    beat = threading.Thread(target=_heartbeat, args=(execution_id, stop), daemon=True)
    beat.start()
    try:
        with session_scope() as session:
            loaded = coordinator.load(session, session.get(Execution, execution_id))
        result = coordinator.compute(team, loaded)
        with session_scope() as session:
            coordinator.apply(session, loaded, result, team)
        joblog.note("job finished")
    except Exception as exc:  # the run must never be left silently stuck
        log.exception("job %s failed", execution_id)
        joblog.note(f"job failed: {type(exc).__name__}: {str(exc)[:500]}")
        _fail(execution_id, exc)
    finally:
        stop.set()
        joblog.stop()
    return True


def _fail(execution_id, exc: Exception) -> None:
    with session_scope() as session:
        execution = session.get(Execution, execution_id, with_for_update=True)
        run = session.get(Run, execution.run_id, with_for_update=True)
        message = f"{type(exc).__name__}: {exc}"[:500]
        if isinstance(exc, CapacityBusy):
            jobs.postpone(session, execution, exc.wait_seconds or get_settings().capacity_wait_seconds, message)
            return
        retryable = not execution.side_effects and not isinstance(exc, InvalidModelOutput)
        if retryable and execution.attempts < get_settings().job_max_attempts:
            jobs.postpone(session, execution, get_settings().job_retry_base_seconds * 2 ** execution.attempts, message)
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

    def __init__(self, idle_release_seconds: float, enabled: bool = True):
        self.idle_release_seconds, self.enabled = idle_release_seconds, enabled
        self.process: subprocess.Popen | None = None
        self.last_work = 0.0

    def busy(self) -> None:
        self.last_work = time.monotonic()
        if self.enabled and self.process is None and sys.platform == "darwin" and shutil.which("caffeinate"):
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
    settings = get_settings()
    awake = StayAwake(settings.keep_awake_release_seconds, enabled=settings.sdlc_keep_awake)
    log.info("worker %s started with team %s", WORKER_ID, team.engine)
    for leftover in process.kill_orphans():
        log.warning("stopped %s left behind by an earlier worker", leftover)
    next_pr_poll = 0.0
    while not stopping.is_set():
        try:
            # Hold the assertion before the job starts: taken only after a job, the first job after an idle spell
            # ran unprotected and the Mac slept through it (discovery trial, 2026-10-16).
            awake.idle(_queue_has_work())
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
                next_pr_poll = time.monotonic() + get_settings().pr_poll_seconds
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
