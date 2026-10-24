"""Background processes started by the CLI (API server, SSH tunnel).

Each process runs in its own session so that stopping it also stops its
children. A PID file records the PID and the command arguments so that a
reused PID is never mistaken for ours. Arguments are compared instead of the
executable because macOS re-executes venv Python as Python.app.
"""

from dataclasses import dataclass
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import time

from controller.app.config import DATA_DIR


def port_listening(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex((host, port)) == 0


def _command_of(pid: int) -> str | None:
    result = subprocess.run(["ps", "-o", "command=", "-p", str(pid)],
                            capture_output=True, text=True, check=False)
    return result.stdout.strip() or None


def platform_env(settings) -> dict[str, str]:
    """The environment of the API, the worker and their health checks: the Claude account is the platform's
    (SDLC_CLAUDE_CONFIG_DIR or the default ~/.claude), not the one of the shell that started them (24/10: a restart
    from a developer's Claude Code session moved the worker to the developer's account)."""
    env = {k: v for k, v in os.environ.items() if k != "CLAUDE_CONFIG_DIR"}
    if settings.sdlc_claude_config_dir:
        env["CLAUDE_CONFIG_DIR"] = os.path.expanduser(settings.sdlc_claude_config_dir)
    return env


@dataclass(frozen=True)
class ManagedProcess:
    name: str
    data_dir: Path = DATA_DIR

    @property
    def pid_file(self) -> Path:
        return self.data_dir / "run" / f"{self.name}.pid"

    @property
    def log_file(self) -> Path:
        return self.data_dir / "log" / f"{self.name}.log"

    def pid(self) -> int | None:
        """PID of our running process, or None. Removes a stale PID file."""
        try:
            record = json.loads(self.pid_file.read_text())
            pid, args = int(record["pid"]), str(record["args"])
        except (OSError, ValueError, KeyError, TypeError):
            return None
        command = _command_of(pid)
        if command is None or not command.endswith(args):
            self.pid_file.unlink(missing_ok=True)
            return None
        return pid

    def start(self, argv: list[str], cwd: Path, env: dict[str, str] | None = None) -> int:
        if self.pid() is not None:
            raise RuntimeError(f"{self.name} is already running")
        self.pid_file.parent.mkdir(parents=True, exist_ok=True)
        self.log_file.parent.mkdir(parents=True, exist_ok=True)
        with self.log_file.open("ab") as log:
            process = subprocess.Popen(
                argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=log,
                stderr=subprocess.STDOUT, start_new_session=True,
            )
        self.pid_file.write_text(json.dumps({"pid": process.pid, "args": " ".join(argv[1:])}))
        return process.pid

    def stop(self, timeout: float = 10) -> bool:
        """Stop our process group. Returns False if nothing was running."""
        pid = self.pid()
        if pid is None:
            return False
        try:
            os.killpg(pid, signal.SIGTERM)
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline and _command_of(pid) is not None:
                time.sleep(0.2)
            if _command_of(pid) is not None:
                os.killpg(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        self.pid_file.unlink(missing_ok=True)
        return True

    def log_tail(self, lines: int = 20) -> str:
        try:
            return "\n".join(self.log_file.read_text(errors="replace").splitlines()[-lines:])
        except OSError:
            return ""


API = ManagedProcess("api")
WORKER = ManagedProcess("worker")
