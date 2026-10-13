"""Local CLI for the Option B pilot controller and audited workers."""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import UUID

from controller.app.config import get_settings


ROOT = Path(__file__).resolve().parents[1]


def request(method: str, path: str, payload: dict | None = None) -> object:
    token = get_settings().controller_token
    url = os.environ.get("SDLC_CONTROLLER_URL", "http://127.0.0.1:8765").rstrip("/") + path
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {"X-Controller-Token": token}
    if data is not None:
        headers["Content-Type"] = "application/json"
    try:
        with urlopen(Request(url, data=data, headers=headers, method=method), timeout=20) as response:
            return json.load(response)
    except HTTPError as exc:
        try:
            detail = json.load(exc).get("detail", exc.reason)
        except (ValueError, AttributeError):
            detail = exc.reason
        raise SystemExit(f"controller HTTP {exc.code}: {detail}") from exc
    except URLError as exc:
        raise SystemExit(f"controller unavailable at {url}: {exc.reason}") from exc


def run_worker(module: str, args: list[str]) -> None:
    subprocess.run([sys.executable, "-m", module, *args, "--execute"], cwd=ROOT, check=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    serve = commands.add_parser("serve", help="start the local web/API server")
    serve.add_argument("--port", type=int, default=8765)
    commands.add_parser("runs", help="list recent runs")
    commands.add_parser("features", help="list fixed benchmark features")
    create = commands.add_parser("create", help="create a run for one fixed benchmark feature")
    create.add_argument("feature")
    for name in ("show", "events", "approve-plan", "reject-plan", "revise-plan", "approve-pr", "cancel", "analyze", "code", "review", "prepare-base", "publish-pr", "reconcile-ci"):
        command = commands.add_parser(name)
        command.add_argument("run_id", type=UUID)
        if name in {"approve-plan", "reject-plan"}:
            command.add_argument("--plan-sha", required=True, help="exact SHA-256 shown by 'show'")
        if name in {"approve-plan", "reject-plan", "revise-plan", "approve-pr", "cancel"}:
            command.add_argument("--actor", required=True, help="human decision maker")
            command.add_argument("--reason", default="")
        if name == "revise-plan":
            command.add_argument("--file", type=Path, required=True, help="JSON file containing the new plan payload")
        if name in {"analyze", "review"}:
            command.add_argument("--gx10-host")
            command.add_argument("--gx10-identity-file")
            command.add_argument("--gx10-port", type=int)
            command.add_argument("--model", choices=("qwen3:14b", "qwen3:30b", "qwen3:30b-instruct"), default="qwen3:30b-instruct")
            command.add_argument("--allow-unhealthy", action="append", default=[])
        if name == "code":
            command.add_argument("--executor", choices=("codex", "claude"), default="codex")
    args = parser.parse_args()
    name = args.command
    if name == "serve":
        subprocess.run([sys.executable, "-m", "uvicorn", "controller.app.main:app", "--host", "127.0.0.1", "--port", str(args.port)], cwd=ROOT, check=True)
        return
    if name == "runs":
        result = request("GET", "/api/runs?limit=100")
    elif name == "features":
        result = [{"id": item["id"]} for item in request("GET", "/api/pilot/features")]
    elif name == "create":
        result = request("POST", f"/api/pilot/runs/{args.feature}")
    elif name in {"show", "events"}:
        suffix = "/events" if name == "events" else ""
        result = request("GET", f"/api/runs/{args.run_id}{suffix}")
    elif name in {"approve-plan", "reject-plan", "revise-plan", "approve-pr", "cancel"}:
        run = request("GET", f"/api/runs/{args.run_id}")
        payload = {"expected_run_version": run["version"], "actor": args.actor, "reason": args.reason or None}
        if name == "revise-plan":
            if not args.reason:
                raise SystemExit("--reason is required when revising a plan")
            try:
                payload["plan"] = json.loads(args.file.read_text())
            except (OSError, ValueError) as exc:
                raise SystemExit(f"cannot read plan JSON: {exc}") from exc
        elif name in {"approve-plan", "reject-plan"}:
            if run["status"] != "WAITING_PLAN_APPROVAL" or not run["plans"]:
                raise SystemExit("run is not waiting for plan approval")
            plan = run["plans"][-1]
            if args.plan_sha != plan["sha256"]:
                raise SystemExit("plan SHA-256 differs from the current plan; inspect 'show' again")
            payload.update({"plan_id": plan["id"], "plan_sha256": plan["sha256"]})
        result = request("POST", f"/api/runs/{args.run_id}/{name}", payload)
    else:
        worker_args = ["--run-id", str(args.run_id)]
        modules = {
            "analyze": "scripts.run_pilot_reasoning", "review": "scripts.run_pilot_reasoning",
            "code": "scripts.run_pilot_coding_worker", "publish-pr": "scripts.run_pilot_pr",
            "reconcile-ci": "scripts.reconcile_pilot_ci", "prepare-base": "scripts.prepare_pilot_base",
        }
        if name in {"analyze", "review"}:
            settings = get_settings()
            host = args.gx10_host or settings.gx10_ssh_host
            identity_file = args.gx10_identity_file or settings.gx10_ssh_identity_file
            port = args.gx10_port or settings.gx10_ssh_port
            if not host or not identity_file:
                raise SystemExit("set GX10_SSH_HOST and GX10_SSH_IDENTITY_FILE or pass the matching flags")
            worker_args.extend(["--stage", "analysis" if name == "analyze" else "review",
                                "--model", args.model, "--gx10-host", host,
                                "--gx10-port", str(port),
                                "--gx10-identity-file", identity_file])
            for container in args.allow_unhealthy:
                worker_args.extend(["--allow-unhealthy", container])
        elif name == "code":
            worker_args.extend(["--executor", args.executor, "--once"])
        run_worker(modules[name], worker_args)
        return
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
