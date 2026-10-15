"""Bounded subprocesses for the doing roles.

Argument arrays only, never a shell. Each process runs in its own session so a
timeout or cancellation kills the whole process group. Output is capped and
secrets are redacted before anything is stored.
"""

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import threading
import time

from controller.app import joblog
from controller.app.config import DATA_DIR

MAX_OUTPUT = 200_000  # characters kept from the end of the combined output
SECRET = re.compile(
    r"(gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|sk-[A-Za-z0-9_-]{20,}|"
    r"(?i:(?:api[_-]?key|token|secret|password)\s*[=:]\s*)\S+)")


def redact(text: str) -> str:
    return SECRET.sub("[redacted]", text)


@dataclass(frozen=True)
class ProcessResult:
    args: list[str]
    exit_code: int | None  # None when the process was killed on timeout
    output: str
    seconds: float
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out

    def tail(self, lines: int = 40) -> str:
        return "\n".join(self.output.rstrip().splitlines()[-lines:])


# Processes and containers of the running job, so a cancellation can stop them, and PID files so that a
# restarted worker can stop sessions a crashed worker left behind.
SESSIONS_DIR = DATA_DIR / "run" / "sessions"
CONTAINER_PREFIX = "sdlc-validate-"
_lock = threading.Lock()
_active: dict[int, subprocess.Popen] = {}
_containers: set[str] = set()
_cancelled: set[int] = set()
_job_cancelled = threading.Event()  # once set, the job starts no further process


def begin_job() -> None:
    _job_cancelled.clear()


def run_bounded(args: list[str], *, cwd: Path | None = None, timeout: float, input_text: str | None = None,
                env: dict[str, str] | None = None, container: str | None = None) -> ProcessResult:
    if not args or not all(isinstance(arg, str) for arg in args):
        raise ValueError("args must be a non-empty list of strings")
    if _job_cancelled.is_set():
        return ProcessResult(list(args), None, "[cancelled: the run was cancelled]", 0.0)
    started = time.monotonic()
    process = subprocess.Popen(args, cwd=cwd, env=env, stdin=subprocess.PIPE if input_text is not None else subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, start_new_session=True,
                               bufsize=1)
    _track(process, args, container)
    # Read the output line by line while the process runs, so the job's live log shows it as it happens.
    log_path, program = joblog.current(), Path(args[0]).name
    joblog.note(f"runs {program}" + (f" in container {container}" if container else ""))
    lines: list[str] = []

    def pump() -> None:
        for line in process.stdout:
            lines.append(line)
            joblog.stream(log_path, program, line)

    reader = threading.Thread(target=pump, daemon=True)
    reader.start()
    if input_text is not None:
        try:
            process.stdin.write(input_text)
            process.stdin.close()
        except (BrokenPipeError, OSError):
            pass
    timed_out = False
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        _kill_group(process)
        process.wait()
    finally:
        reader.join(timeout=10)
        cancelled = _untrack(process, container)
    output = redact("".join(lines))[-MAX_OUTPUT:]
    joblog.note(f"{program} {'timed out' if timed_out else 'cancelled' if cancelled else f'exited {process.returncode}'} "
                f"after {round(time.monotonic() - started)}s")
    if cancelled:
        output += "\n[cancelled: the run was cancelled]"
    return ProcessResult(list(args), None if timed_out or cancelled else process.returncode, output,
                         round(time.monotonic() - started, 2), timed_out)


def _track(process: subprocess.Popen, args: list[str], container: str | None) -> None:
    with _lock:
        _active[process.pid] = process
        if container:
            _containers.add(container)
    try:
        SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
        (SESSIONS_DIR / f"{process.pid}.json").write_text(json.dumps({"pid": process.pid, "program": args[0],
                                                                      "container": container}))
    except OSError:
        pass


def _untrack(process: subprocess.Popen, container: str | None) -> bool:
    with _lock:
        _active.pop(process.pid, None)
        if container:
            _containers.discard(container)
        cancelled = process.pid in _cancelled
        _cancelled.discard(process.pid)
    (SESSIONS_DIR / f"{process.pid}.json").unlink(missing_ok=True)
    return cancelled


def cancel_active() -> int:
    """Stop every process and container of the running job (the run was cancelled). Returns how many."""
    _job_cancelled.set()
    with _lock:
        processes, containers = list(_active.values()), list(_containers)
        _cancelled.update(p.pid for p in processes)
    for name in containers:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=30, check=False)
    for process in processes:
        _kill_group(process)
    return len(processes)


def kill_orphans() -> list[str]:
    """At worker start: stop sessions and containers left behind by a worker that died."""
    stopped = []
    for path in SESSIONS_DIR.glob("*.json") if SESSIONS_DIR.exists() else []:
        try:
            info = json.loads(path.read_text())
            command = subprocess.run(["ps", "-o", "command=", "-p", str(info["pid"])], capture_output=True,
                                     text=True, check=False).stdout
            if Path(info["program"]).name in command:  # the PID was not reused by something else
                os.killpg(info["pid"], signal.SIGKILL)
                stopped.append(f"{info['program']} pid {info['pid']}")
        except (OSError, ValueError, KeyError):
            pass
        path.unlink(missing_ok=True)
    names = subprocess.run(["docker", "ps", "-aq", "--filter", f"name={CONTAINER_PREFIX}"], capture_output=True,
                           text=True, check=False).stdout.split()
    for name in names:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=30, check=False)
        stopped.append(f"container {name}")
    return stopped


def _kill_group(process: subprocess.Popen) -> None:
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(process.pid, sig)
        except ProcessLookupError:
            return
        try:
            process.wait(timeout=5)
            return
        except subprocess.TimeoutExpired:
            continue
