"""SSH tunnel from the Mac to Ollama on GX10."""

from dataclasses import dataclass
from pathlib import Path
import time

from controller.app.config import PROJECT_ROOT, Settings
from controller.app.processes import ManagedProcess, port_listening

TUNNEL = ManagedProcess("gx10-tunnel")


@dataclass(frozen=True)
class TunnelStatus:
    state: str  # "owned" (started by sdlc), "external" (another process listens), "down"
    pid: int | None = None


def tunnel_argv(settings: Settings) -> list[str]:
    forward = f"127.0.0.1:{settings.ollama_tunnel_port}:127.0.0.1:{settings.ollama_remote_port}"
    return [
        "ssh", "-N",
        "-o", "BatchMode=yes",
        "-o", "ExitOnForwardFailure=yes",
        "-o", "ServerAliveInterval=30",
        "-o", "ServerAliveCountMax=3",
        "-p", str(settings.gx10_ssh_port),
        "-i", str(Path(settings.gx10_ssh_identity_file).expanduser()),
        "-L", forward,
        "--", settings.gx10_ssh_host,
    ]


def tunnel_status(settings: Settings) -> TunnelStatus:
    pid = TUNNEL.pid()
    if pid is not None:
        return TunnelStatus("owned", pid)
    if port_listening(settings.ollama_tunnel_port):
        return TunnelStatus("external")
    return TunnelStatus("down")


def ensure_tunnel(settings: Settings, wait_seconds: float | None = None) -> TunnelStatus:
    status = tunnel_status(settings)
    if status.state != "down":
        return status
    problems = settings.gx10_problems()
    if problems:
        raise RuntimeError("; ".join(problems))
    TUNNEL.start(tunnel_argv(settings), cwd=PROJECT_ROOT)
    deadline = time.monotonic() + (settings.tunnel_wait_seconds if wait_seconds is None else wait_seconds)
    while time.monotonic() < deadline:
        if port_listening(settings.ollama_tunnel_port):
            return tunnel_status(settings)
        if TUNNEL.pid() is None:
            break
        time.sleep(0.5)
    TUNNEL.stop()
    raise RuntimeError(f"SSH tunnel did not open. Log:\n{TUNNEL.log_tail()}")


def stop_tunnel() -> bool:
    return TUNNEL.stop()
