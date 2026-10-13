"""Bounded subprocesses for the doing roles.

Argument arrays only, never a shell. Each process runs in its own session so a
timeout or cancellation kills the whole process group. Output is capped and
secrets are redacted before anything is stored.
"""

from dataclasses import dataclass
import os
from pathlib import Path
import re
import signal
import subprocess
import time

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


def run_bounded(args: list[str], *, cwd: Path | None = None, timeout: float, input_text: str | None = None,
                env: dict[str, str] | None = None) -> ProcessResult:
    if not args or not all(isinstance(arg, str) for arg in args):
        raise ValueError("args must be a non-empty list of strings")
    started = time.monotonic()
    process = subprocess.Popen(args, cwd=cwd, env=env, stdin=subprocess.PIPE if input_text is not None else subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, start_new_session=True)
    timed_out = False
    try:
        output, _ = process.communicate(input_text, timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        _kill_group(process)
        output, _ = process.communicate()
    output = redact(output or "")[-MAX_OUTPUT:]
    return ProcessResult(list(args), None if timed_out else process.returncode, output,
                         round(time.monotonic() - started, 2), timed_out)


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
