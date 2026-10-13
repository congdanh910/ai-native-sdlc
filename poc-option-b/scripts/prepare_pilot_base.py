"""Create a feature-specific benchmark PR base at the immutable pilot commit."""

import argparse
import json
import subprocess
from pathlib import Path
from uuid import UUID

from sqlalchemy.orm import Session

from controller.app.config import get_settings
from controller.app.db import get_engine
from controller.app.services import get_run
from scripts.pilot_branches import feature_id_for_requirement, pr_base_branch


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=45)
    if result.returncode:
        raise SystemExit(f"git {args[0]} failed; benchmark base was not prepared")
    return result.stdout.strip()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", type=UUID, required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    if not args.execute:
        parser.error("pass --execute to allow creating a remote benchmark branch")

    root = Path(__file__).resolve().parents[1]
    manifest = json.loads((root / "fixtures/task-board/benchmark.json").read_text())
    repo_id = manifest["repository"].removeprefix("https://github.com/").removesuffix(".git")
    if repo_id not in get_settings().repo_allowlist:
        raise SystemExit("pilot repository is not allowlisted")
    with Session(get_engine()) as session:
        run = get_run(session, args.run_id)
        if (run.repo_id != repo_id or run.base_commit != manifest["base_commit"]
                or run.status != "PR_READY"):
            raise SystemExit("run is not a reviewed pinned pilot ready for PR")
        feature = feature_id_for_requirement(manifest, run.requirement, root)
    branch = pr_base_branch(manifest, feature)
    repo = root / ".data/repos/ai-native-product-demo"
    if git(repo, "remote", "get-url", "origin") != manifest["repository"]:
        raise SystemExit("pilot clone origin differs from manifest")
    if git(repo, "rev-parse", f"{manifest['base_commit']}^{{commit}}") != manifest["base_commit"]:
        raise SystemExit("pinned benchmark commit is missing")
    ref = f"refs/heads/{branch}"
    remote = git(repo, "ls-remote", "--heads", "origin", ref)
    expected = f"{manifest['base_commit']}\t{ref}"
    if remote:
        if remote != expected:
            raise SystemExit("remote benchmark branch moved from the pinned commit")
        print(f"{branch} already points to {manifest['base_commit']}")
        return
    git(repo, "push", "origin", f"{manifest['base_commit']}:{ref}")
    if git(repo, "ls-remote", "--heads", "origin", ref) != expected:
        raise SystemExit("remote benchmark branch did not verify after push")
    print(f"created {branch} at {manifest['base_commit']}")


if __name__ == "__main__":
    main()
