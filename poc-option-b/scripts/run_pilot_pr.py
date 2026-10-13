"""Publish one evidence-backed draft PR for a reviewed benchmark feature."""

import argparse
import asyncio
import json
import subprocess
from pathlib import Path
from uuid import UUID

from sqlalchemy.orm import sessionmaker

from controller.app.config import get_settings
from controller.app.db import get_engine
from controller.app.pr_worker import publish_draft_pr
from controller.app.services import get_run
from scripts.pilot_branches import feature_id_for_requirement, pr_base_branch


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", type=UUID, required=True)
    parser.add_argument("--execute", action="store_true", help="allow Git push and draft PR creation")
    args = parser.parse_args()
    if not args.execute:
        parser.error("pass --execute to enable external GitHub writes")
    project_root = Path(__file__).resolve().parents[1]
    manifest = json.loads((project_root / "fixtures/task-board/benchmark.json").read_text())
    repo_id = manifest["repository"].removeprefix("https://github.com/").removesuffix(".git")
    if repo_id not in get_settings().repo_allowlist:
        raise SystemExit("pilot repository is not in ALLOWED_REPOS")
    sessions = sessionmaker(get_engine(), expire_on_commit=False)
    with sessions() as session:
        run = get_run(session, args.run_id)
        if run.repo_id != repo_id or run.base_commit != manifest["base_commit"] or run.status != "PR_READY":
            raise SystemExit("run is not a reviewed run on the pinned pilot commit")
        try:
            feature_id = feature_id_for_requirement(manifest, run.requirement, project_root)
            base_branch = pr_base_branch(manifest, feature_id)
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc
    pilot_repo = project_root / ".data/repos/ai-native-product-demo"
    origin = subprocess.run(
        ["git", "-C", str(pilot_repo), "remote", "get-url", "origin"],
        capture_output=True, text=True, timeout=15,
    )
    if origin.returncode or origin.stdout.strip() != manifest["repository"]:
        raise SystemExit("pilot clone origin does not match the benchmark repository")
    remote_base = subprocess.run(
        ["git", "-C", str(pilot_repo), "ls-remote", "--heads", "origin", f"refs/heads/{base_branch}"],
        capture_output=True, text=True, timeout=30,
    )
    if (remote_base.returncode or remote_base.stdout.strip()
            != f"{manifest['base_commit']}\trefs/heads/{base_branch}"):
        raise SystemExit("PR base branch is missing or no longer points to the pinned benchmark commit")
    auth = subprocess.run(["gh", "auth", "status", "-h", "github.com"], capture_output=True, timeout=15)
    if auth.returncode:
        raise SystemExit("GitHub CLI authentication is unavailable; no branch was pushed")
    url = asyncio.run(publish_draft_pr(
        sessions, args.run_id, repo_full_name=repo_id,
        baseline_branch=base_branch,
        worktree_root=project_root / ".data/worktrees",
        artifact_root=project_root / ".data/artifacts",
    ))
    print(url)


if __name__ == "__main__":
    main()
