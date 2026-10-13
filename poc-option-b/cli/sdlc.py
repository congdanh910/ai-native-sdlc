"""Operator CLI: ./sdlc init | up | down | doctor | logs."""

import argparse
import getpass
import json
import sys
import time

import httpx

from cli.envfile import write_env
from controller.app import health, migrations
from controller.app.compose import SERVICES, run_compose
from controller.app.config import ENV_FILE, PROJECT_ROOT, Settings
from controller.app.db import get_engine
from controller.app.integrations.langflow import LangflowError, client_from_settings
from controller.app.processes import API, WORKER, port_listening
from controller.app.tunnel import TUNNEL, ensure_tunnel, stop_tunnel

SYMBOLS = {health.OK: "✓", health.WARN: "!", health.FAIL: "✗"}


class CliError(Exception):
    pass


def say(message: str) -> None:
    print(message, flush=True)


def print_checks(checks: list[health.Check]) -> None:
    width = max(len(check.name) for check in checks)
    for check in checks:
        say(f"  {SYMBOLS[check.status]} {check.name.ljust(width)}  {check.detail}")
        if check.hint and check.status != health.OK:
            say(f"    {' ' * width} → {check.hint}")


def cmd_init(args: argparse.Namespace) -> int:
    values = {"SDLC_OPERATOR": args.operator or getpass.getuser()}
    optional = {"GX10_SSH_HOST": args.gx10_host, "GX10_SSH_PORT": args.gx10_port,
                "GX10_SSH_IDENTITY_FILE": args.gx10_identity, "OLLAMA_REMOTE_PORT": args.ollama_remote_port}
    values.update({key: str(value) for key, value in optional.items() if value is not None})
    try:
        write_env(ENV_FILE, PROJECT_ROOT / ".env.example", values)
    except (FileExistsError, ValueError) as exc:
        raise CliError(str(exc)) from exc
    say(f"Created {ENV_FILE} with generated secrets (mode 600).")
    missing = Settings().placeholders()
    if missing:
        say("Still to fill in: " + ", ".join(missing))
    say("Next: ./sdlc up")
    return 0


def _require_config(settings: Settings) -> None:
    check = health.check_config(settings)
    if check.status == health.FAIL:
        raise CliError(f"{check.detail}. {check.hint}")


def _ensure_api(settings: Settings, wait_seconds: float = 20) -> str:
    if API.pid() is not None:
        return "already running"
    if port_listening(settings.sdlc_api_port):
        raise CliError(f"port {settings.sdlc_api_port} is used by another process; change SDLC_API_PORT")
    API.start([sys.executable, "-m", "uvicorn", "controller.app.main:app",
               "--host", "127.0.0.1", "--port", str(settings.sdlc_api_port)], cwd=PROJECT_ROOT)
    deadline = time.monotonic() + wait_seconds
    while time.monotonic() < deadline:
        try:
            if httpx.get(f"{settings.api_url}/health/live", timeout=1).status_code == 200:
                return "started"
        except httpx.HTTPError:
            pass
        if API.pid() is None:
            break
        time.sleep(0.5)
    API.stop()
    raise CliError(f"API did not start. Log:\n{API.log_tail()}")


def _ensure_worker() -> str:
    if WORKER.pid() is not None:
        return "already running"
    WORKER.start([sys.executable, "-m", "controller.app.worker"], cwd=PROJECT_ROOT)
    time.sleep(1)
    if WORKER.pid() is None:
        raise CliError(f"worker did not start. Log:\n{WORKER.log_tail()}")
    return "started"


def cmd_up(args: argparse.Namespace) -> int:
    settings = Settings()
    _require_config(settings)
    docker = health.check_docker()
    if docker.status == health.FAIL:
        raise CliError(f"{docker.detail}. {docker.hint}")

    say("1/5 Starting PostgreSQL and Langflow (first start can take a few minutes)…")
    result = run_compose("up", "-d", "--wait", *SERVICES)
    if result.returncode:
        raise CliError(f"docker compose failed:\n{result.stderr.strip()}")

    say("2/5 Applying database migrations…")
    migrations.upgrade_to_head(get_engine())

    say("3/5 Syncing role prompts into Langflow flows…")
    try:
        changes = client_from_settings(settings).sync_flows(settings.ollama_model, settings.ollama_url_from_langflow,
                                                           settings.ollama_num_ctx)
    except LangflowError as exc:
        raise CliError(f"cannot sync Langflow flows: {exc}") from exc
    say("    " + ("; ".join(changes) if changes else "flows already match langflow/prompts"))

    say("4/5 Opening the SSH tunnel to GX10…")
    try:
        tunnel = ensure_tunnel(settings)
    except RuntimeError as exc:
        raise CliError(str(exc)) from exc
    say(f"    tunnel {tunnel.state}")

    say("5/5 Starting the controller API and the team worker…")
    say(f"    API {_ensure_api(settings)}")
    say(f"    worker {_ensure_worker()}")

    say("\nHealth:")
    checks = health.doctor_checks(settings, get_engine())
    print_checks(checks)
    say(f"\nControl room: {settings.api_url}")
    return 0 if health.overall(checks) != health.FAIL else 1


def cmd_down(args: argparse.Namespace) -> int:
    say("worker " + ("stopped" if WORKER.stop() else "was not running"))
    say("API " + ("stopped" if API.stop() else "was not running"))
    say("SSH tunnel " + ("stopped" if stop_tunnel() else "not started by sdlc; left as is"))
    if ENV_FILE.is_file():
        result = run_compose("stop", *SERVICES)
        if result.returncode:
            raise CliError(f"docker compose failed:\n{result.stderr.strip()}")
        say("PostgreSQL and Langflow stopped (data kept)")
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    settings = Settings()
    checks = health.doctor_checks(settings, get_engine())
    status = health.overall(checks)
    if args.json:
        say(json.dumps({"status": status, "checks": [check.as_dict() for check in checks]}, indent=2))
    else:
        print_checks(checks)
        say(f"\nOverall: {status}")
    return 0 if status != health.FAIL else 1


def cmd_logs(args: argparse.Namespace) -> int:
    managed = {"api": API, "worker": WORKER, "gx10-tunnel": TUNNEL}
    if args.service in managed:
        say(managed[args.service].log_tail(args.lines) or "(no log yet)")
        return 0
    result = run_compose("logs", "--no-color", "--tail", str(args.lines), args.service, capture=False)
    return result.returncode


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sdlc", description="AI-native SDLC POC operator CLI")
    commands = parser.add_subparsers(dest="command", required=True)

    init = commands.add_parser("init", help="create .env with generated secrets")
    init.add_argument("--operator", help="your name for the audit trail (default: login name)")
    init.add_argument("--gx10-host", help="SSH destination, for example user@gx10-host")
    init.add_argument("--gx10-port", type=int)
    init.add_argument("--gx10-identity", help="path to the SSH private key")
    init.add_argument("--ollama-remote-port", type=int, help="Ollama port on GX10 loopback")
    init.set_defaults(func=cmd_init)

    commands.add_parser("up", help="start services, tunnel, API and worker").set_defaults(func=cmd_up)
    commands.add_parser("down", help="stop worker, API, tunnel and services; keep data").set_defaults(func=cmd_down)

    doctor = commands.add_parser("doctor", help="check every component")
    doctor.add_argument("--json", action="store_true")
    doctor.set_defaults(func=cmd_doctor)

    logs = commands.add_parser("logs", help="show recent logs of one component")
    logs.add_argument("service", choices=["api", "worker", "gx10-tunnel", *SERVICES])
    logs.add_argument("--lines", type=int, default=100)
    logs.set_defaults(func=cmd_logs)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except CliError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
