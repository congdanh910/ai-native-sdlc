"""Create a fresh Git worktree from an allowlisted repository and pinned commit."""

import re
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from controller.app.executors.process import run_process


COMMIT_RE = re.compile(r"^[0-9a-f]{40}([0-9a-f]{24})?$")
SLUG_RE = re.compile(r"^[a-zA-Z0-9_-]{1,60}$")


class WorkspaceError(RuntimeError):
    pass


@dataclass(frozen=True)
class Workspace:
    path: Path
    branch: str
    base_commit: str


async def create_worktree(
    repo_path: Path, *, allowlisted_repo: Path, worktree_root: Path,
    base_commit: str, task_slug: str, executor: str,
) -> Workspace:
    repo = repo_path.resolve(strict=True)
    if repo != allowlisted_repo.resolve(strict=True):
        raise WorkspaceError("repository is not allowlisted")
    if not COMMIT_RE.fullmatch(base_commit) or not SLUG_RE.fullmatch(task_slug):
        raise WorkspaceError("invalid commit or task slug")
    if executor not in {"codex", "claude"}:
        raise WorkspaceError("unsupported executor")
    if worktree_root.is_symlink():
        raise WorkspaceError("worktree root is a symlink")
    worktree_root.mkdir(parents=True, exist_ok=True)
    root = worktree_root.resolve(strict=True)
    if root == repo or repo in root.parents or root in repo.parents:
        raise WorkspaceError("worktree root must be separate from repository")
    top = await run_process(["git", "rev-parse", "--show-toplevel"], repo, timeout_seconds=15)
    if top.returncode or Path(top.stdout.strip()).resolve() != repo:
        raise WorkspaceError("source path is not the repository root")
    commit = await run_process(
        ["git", "rev-parse", "--verify", f"{base_commit}^{{commit}}"], repo, timeout_seconds=15,
    )
    if commit.returncode or commit.stdout.strip().lower() != base_commit:
        raise WorkspaceError("pinned commit is unavailable")
    nonce = uuid4().hex[:12]
    branch = f"sdlc/{executor}/{task_slug}-{nonce}"
    path = root / f"{executor}-{task_slug}-{nonce}"
    if path.exists() or path.is_symlink():
        raise WorkspaceError("worktree path already exists")
    result = await run_process(
        ["git", "worktree", "add", "-b", branch, str(path), base_commit],
        repo, timeout_seconds=60,
    )
    if result.returncode:
        raise WorkspaceError(f"git worktree add failed: {result.stderr}")
    return Workspace(path, branch, base_commit)


async def resume_worktree(
    path: Path, branch: str, expected_commit: str, worktree_root: Path,
) -> Workspace:
    root = worktree_root.resolve(strict=True)
    resolved = path.resolve(strict=True)
    if resolved.parent != root or not COMMIT_RE.fullmatch(expected_commit):
        raise WorkspaceError("saved worktree is outside the pilot root")
    top = await run_process(["git", "rev-parse", "--show-toplevel"], resolved, timeout_seconds=15)
    head = await run_process(["git", "rev-parse", "HEAD"], resolved, timeout_seconds=15)
    current_branch = await run_process(["git", "branch", "--show-current"], resolved, timeout_seconds=15)
    status = await run_process(["git", "status", "--porcelain", "-z"], resolved, timeout_seconds=15)
    if any(result.returncode or result.stdout_truncated for result in (top, head, current_branch, status)):
        raise WorkspaceError("saved worktree cannot be verified")
    if (Path(top.stdout.strip()).resolve() != resolved or head.stdout.strip() != expected_commit
            or current_branch.stdout.strip() != branch or status.stdout):
        raise WorkspaceError("saved worktree changed after the previous task")
    return Workspace(resolved, branch, expected_commit)


async def commit_worktree(path: Path, expected_commit: str, changed_paths: list[str], task_slug: str) -> str:
    if not COMMIT_RE.fullmatch(expected_commit) or not SLUG_RE.fullmatch(task_slug) or not changed_paths:
        raise WorkspaceError("invalid integration commit input")
    head = await run_process(["git", "rev-parse", "HEAD"], path, timeout_seconds=15)
    if head.returncode or head.stdout.strip() != expected_commit:
        raise WorkspaceError("worktree HEAD changed during task")
    staged = await run_process(["git", "add", "-A", "--", *changed_paths], path, timeout_seconds=15)
    if staged.returncode:
        raise WorkspaceError("could not stage approved task changes")
    commit = await run_process([
        "git", "-c", "core.hooksPath=/dev/null", "-c", "user.name=SDLC Controller",
        "-c", "user.email=sdlc-controller@example.invalid",
        "commit", "-m", f"pilot: {task_slug}", "--", *changed_paths,
    ], path, timeout_seconds=30)
    if commit.returncode:
        raise WorkspaceError("could not commit approved task changes")
    new_head = await run_process(["git", "rev-parse", "HEAD"], path, timeout_seconds=15)
    status = await run_process(["git", "status", "--porcelain", "-z"], path, timeout_seconds=15)
    if new_head.returncode or not COMMIT_RE.fullmatch(new_head.stdout.strip()) or status.returncode or status.stdout:
        raise WorkspaceError("integration worktree is not clean after commit")
    return new_head.stdout.strip()
