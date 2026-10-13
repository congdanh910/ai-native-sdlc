"""Run one pilot Langflow stage after a fresh read-only GX10 capacity check."""

import argparse
import asyncio
import json
import subprocess
import sys
from pathlib import Path
from uuid import UUID

from sqlalchemy.orm import sessionmaker

from controller.app.config import get_settings
from controller.app.db import get_engine
from controller.app.integrations.langflow import client_from_settings
from controller.app.model_preflight import PILOT_MODELS, verify_model_binding
from controller.app.reasoning_worker import ReasoningPolicy, analyze_and_plan
from controller.app.review_worker import review_run
from controller.app.services import get_run


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", type=UUID, required=True)
    parser.add_argument("--stage", choices=("analysis", "review"), required=True)
    parser.add_argument("--model", choices=sorted(PILOT_MODELS), default="qwen3:30b")
    parser.add_argument("--gx10-host", required=True)
    parser.add_argument("--gx10-port", type=int, default=2222)
    parser.add_argument("--gx10-identity-file", type=Path, required=True)
    parser.add_argument("--allow-unhealthy", action="append", default=[], metavar="CONTAINER")
    parser.add_argument("--execute", action="store_true", help="allow real Langflow inference")
    args = parser.parse_args()
    if not args.execute:
        parser.error("pass --execute to enable real inference")
    project_root = Path(__file__).resolve().parents[1]
    manifest = json.loads((project_root / "fixtures/task-board/benchmark.json").read_text())
    settings = get_settings()
    repo_id = manifest["repository"].removeprefix("https://github.com/").removesuffix(".git")
    policy = ReasoningPolicy(
        repo_path=project_root / ".data/repos/ai-native-product-demo",
        artifact_root=project_root / ".data/artifacts",
        agent_context_sha256=manifest["agent_context_sha256"],
        requirements_flow_id=settings.langflow_requirements_flow_id,
        planning_flow_id=settings.langflow_planning_flow_id,
        review_flow_id=settings.langflow_review_flow_id,
    )
    sessions = sessionmaker(get_engine(), expire_on_commit=False)
    with sessions() as session:
        run = get_run(session, args.run_id)
        expected_status = "RECEIVED" if args.stage == "analysis" else "VALIDATING"
        if run.repo_id != repo_id or run.base_commit != manifest["base_commit"]:
            raise SystemExit("run does not match the pinned pilot repository and commit")
        if run.status != expected_status:
            raise SystemExit(f"run must be {expected_status} for this stage")
    client = client_from_settings(settings)
    flow_ids = (
        [policy.requirements_flow_id, policy.planning_flow_id]
        if args.stage == "analysis" else [policy.review_flow_id]
    )
    try:
        verify_model_binding(client, flow_ids, model=args.model)
    except Exception as exc:
        raise SystemExit(f"model binding preflight failed: {type(exc).__name__}: {exc}") from exc
    gate_command = [
        sys.executable, "-m", "scripts.check_gx10_capacity",
        "--host", args.gx10_host, "--port", str(args.gx10_port),
        "--identity-file", str(args.gx10_identity_file),
    ]
    for name in args.allow_unhealthy:
        gate_command.extend(["--allow-unhealthy", name])
    gate = subprocess.run(gate_command, check=False)
    if gate.returncode:
        raise SystemExit("GX10 capacity gate failed; no Langflow inference was requested")
    if args.stage == "analysis":
        analyze_and_plan(sessions, args.run_id, policy, client)
    else:
        asyncio.run(review_run(
            sessions, args.run_id, policy,
            project_root / ".data/worktrees", client,
        ))


if __name__ == "__main__":
    main()
