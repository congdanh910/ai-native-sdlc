"""Previews: the application of a run's branch started with docker compose, for people to try and for the acceptance
tests.

A preview opens one service on 127.0.0.1 through an override file the platform writes (the project's compose file
names no host port). It stops after SDLC_PREVIEW_TTL_MINUTES, when the run ends, or when a newer preview needs its
place (SDLC_PREVIEW_MAX_RUNNING). It reaches the network like an application a developer runs on the machine.
"""

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import socket
import time
import urllib.error
import urllib.request

from controller.app.config import get_settings
from controller.app.doing.process import run_bounded
from controller.app.projects.config import Preview

PREFIX = "sdlc-preview-"


def project_name(run_id: str) -> str:
    return f"{PREFIX}{run_id[:8]}"


def port_range() -> range:
    first, _, last = get_settings().sdlc_preview_ports.partition("-")
    return range(int(first), int(last or first) + 1)


def free_port(taken: set[int]) -> int | None:
    """The first port of the range that no preview holds and nothing else listens on."""
    for port in port_range():
        if port in taken:
            continue
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            if probe.connect_ex(("127.0.0.1", port)) != 0:
                return port
    return None


def missing_parts(worktree: Path, preview: Preview) -> list[str]:
    """What the preview cannot start without: its compose file, and the build folders its services name (the code a
    run has not written yet, such as backend/ before the foundation's coding)."""
    import yaml
    compose_file = worktree / preview.compose
    if not compose_file.is_file():
        return [preview.compose]
    try:
        services = (yaml.safe_load(compose_file.read_text()) or {}).get("services") or {}
    except yaml.YAMLError:
        return []  # docker compose reports the error itself
    missing = []
    for service in services.values():
        build = service.get("build") if isinstance(service, dict) else None
        context = build.get("context") if isinstance(build, dict) else build
        if isinstance(context, str) and not context.startswith(("http://", "https://", "git@")):
            name = context.removeprefix("./").rstrip("/") + "/"
            if not (worktree / context).resolve().is_dir() and name not in missing:
                missing.append(name)
    return missing


def _compose(run_id: str, worktree: Path, preview: Preview, override: Path | None, *args: str) -> list[str]:
    files = ["-f", str(worktree / preview.compose)] + (["-f", str(override)] if override else [])
    return ["docker", "compose", "-p", project_name(run_id), "--project-directory", str(worktree), *files, *args]


def start(run_id: str, worktree: Path, preview: Preview, port: int, log_dir: Path) -> dict:
    """Build and start the preview, wait for its health path; returns the state to keep (status, url, port ...)."""
    log_dir.mkdir(parents=True, exist_ok=True)
    missing = missing_parts(worktree, preview)
    if missing:
        return {"status": "failed", "detail": f"nothing to run yet: {', '.join(missing)} not in the run's branch "
                                              "(the run's coding creates them)"}
    override = log_dir / "preview.override.yml"
    # ports as a list of one mapping: the platform's host port, on the loopback interface only
    override.write_text(json.dumps({"services": {preview.service: {"ports": [f"127.0.0.1:{port}:{preview.port}"]}}}))
    result = run_bounded(_compose(run_id, worktree, preview, override, "up", "-d", "--build", "--remove-orphans"),
                         timeout=preview.start_timeout_seconds)
    (log_dir / "preview-up.log").write_text(result.output)
    if not result.ok:
        failed = _failed_services(run_id, worktree, preview, override)
        stop(run_id, worktree, preview)
        return {"status": "failed", "detail": f"docker compose up failed (exit {result.exit_code})"
                                              + "".join(f"\n\n{name}:\n{log}" for name, log in failed)
                                              + f"\n\ncompose:\n{result.tail(8)}"}
    base = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + preview.start_timeout_seconds
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(base + preview.health_path, timeout=5) as answer:  # noqa: S310 (loopback)
                if answer.status < 400:
                    now = datetime.now(timezone.utc)
                    return {"status": "running", "url": base + preview.path, "port": port,
                            "project": project_name(run_id), "started_at": now.isoformat(),
                            "expires_at": (now + timedelta(minutes=get_settings().sdlc_preview_ttl_minutes)).isoformat(),
                            "detail": f"{preview.service} answers on {base}{preview.health_path}"}
        except (urllib.error.URLError, OSError, ValueError):
            pass
        time.sleep(2)
    logs = run_bounded(_compose(run_id, worktree, preview, override, "logs", "--tail", "60"), timeout=60)
    stop(run_id, worktree, preview)
    return {"status": "failed", "detail": f"{preview.health_path} did not answer within {preview.start_timeout_seconds}s:"
                                          f"\n{logs.tail(40)}"}


def _failed_services(run_id: str, worktree: Path, preview: Preview, override: Path) -> list[tuple[str, str]]:
    """The services that exited with an error, and the end of their logs: the reason compose itself does not print."""
    listed = run_bounded(_compose(run_id, worktree, preview, override, "ps", "-a", "--format", "json"), timeout=60)
    failed, services = [], []
    for line in listed.output.splitlines():  # one object per line; older compose versions print one array
        try:
            parsed = json.loads(line)
        except ValueError:
            continue
        services += parsed if isinstance(parsed, list) else [parsed]
    for service in services:
        if service.get("State") == "exited" and service.get("ExitCode", 0) != 0:
            logs = run_bounded(_compose(run_id, worktree, preview, override, "logs", "--no-log-prefix", "--tail", "15",
                                        service["Service"]), timeout=60)
            failed.append((f"{service['Service']} (exit {service['ExitCode']})", logs.tail(15)))
    return failed


def stop(run_id: str, worktree: Path | None = None, preview: Preview | None = None) -> dict:
    """Stop and remove the preview's containers, networks and volumes (its data is test data)."""
    result = run_bounded(["docker", "compose", "-p", project_name(run_id), "down", "-v", "--remove-orphans"],
                         timeout=180)
    return {"status": "stopped", "detail": "stopped" if result.ok else f"docker compose down: {result.tail(5)}"}


def network(run_id: str) -> str:
    """The default network of the preview's compose project, which the acceptance tests join."""
    return f"{project_name(run_id)}_default"


def expired(state: dict, now: datetime | None = None) -> bool:
    expires = state.get("expires_at")
    return bool(expires) and datetime.fromisoformat(expires) <= (now or datetime.now(timezone.utc))
