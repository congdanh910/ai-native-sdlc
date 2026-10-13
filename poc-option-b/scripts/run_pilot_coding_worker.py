"""Poll approved Task Board executions; explicit --execute starts coding CLIs."""

import argparse
import asyncio
import json
import socket
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy.orm import sessionmaker

from controller.app.coding_worker import RepoPolicy, execute_once
from controller.app.config import get_settings
from controller.app.db import get_engine
from controller.app.services import get_run
from controller.app.worker import reconcile_expired_executions


PILOT_VALIDATION_IMAGE = "node@sha256:0a7108bf6c7bf5de370ffb1a3ed6be93d405b43ff159f681a8d18c0e2bc2e402"


def pilot_policy(project_root: Path) -> RepoPolicy:
    manifest = json.loads((project_root / "fixtures/task-board/benchmark.json").read_text())
    repo_id = manifest["repository"].removeprefix("https://github.com/").removesuffix(".git")
    return RepoPolicy(
        repo_id=repo_id,
        repo_path=project_root / ".data/repos/ai-native-product-demo",
        worktree_root=project_root / ".data/worktrees",
        agent_context_sha256=manifest["agent_context_sha256"],
        validation_commands=tuple(manifest["validation_commands"]),
        validation_container_image=PILOT_VALIDATION_IMAGE,
    )


async def poll(executor: str, run_id: UUID, once: bool, interval_seconds: float) -> None:
    project_root = Path(__file__).resolve().parents[1]
    manifest = json.loads((project_root / "fixtures/task-board/benchmark.json").read_text())
    policy = pilot_policy(project_root)
    settings = get_settings()
    if policy.repo_id not in settings.repo_allowlist:
        raise SystemExit("pilot repository is not in ALLOWED_REPOS")
    if not policy.repo_path.is_dir():
        raise SystemExit("pilot clone is missing")
    sessions = sessionmaker(get_engine(), expire_on_commit=False)
    with sessions() as session:
        run = get_run(session, run_id)
        if (run.repo_id != policy.repo_id or run.executor != executor
                or run.base_commit != manifest["base_commit"] or run.status not in {"READY", "CODING"}):
            raise SystemExit("run does not match the approved pinned pilot")
    worker_id = f"{socket.gethostname()}:{executor}:{uuid4().hex[:12]}"
    while True:
        with sessions() as session:
            expired = reconcile_expired_executions(session)
            session.commit()
        if expired:
            print(f"Marked {len(expired)} expired execution(s) UNKNOWN; review before retry")
        execution_id = await execute_once(
            sessions, {policy.repo_id: policy},
            worker_id=worker_id, executor_name=executor,
            run_id_filter=run_id,
        )
        if execution_id is not None:
            print(f"Processed execution {execution_id}; inspect run status and audit events")
        if once:
            return
        if execution_id is None:
            await asyncio.sleep(interval_seconds)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--executor", choices=("codex", "claude"), required=True)
    parser.add_argument("--run-id", type=UUID, required=True)
    parser.add_argument("--execute", action="store_true", help="allow real coding CLI calls")
    parser.add_argument("--once", action="store_true", help="process at most one execution")
    parser.add_argument("--poll-seconds", type=float, default=3)
    args = parser.parse_args()
    if not args.execute:
        parser.error("pass --execute to enable real coding CLI calls")
    if args.poll_seconds <= 0:
        parser.error("poll interval must be positive")
    asyncio.run(poll(args.executor, args.run_id, args.once, args.poll_seconds))


if __name__ == "__main__":
    main()
