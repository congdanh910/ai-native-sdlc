"""Path and diff checks after each doing role, and the commits the controller makes for them.

The CLIs never commit or push. After a session the controller stages what
changed in the run worktree, checks it against the role's rules, and either
commits it under the role's name or discards it. These checks are authoritative;
the in-session guards only stop mistakes earlier.
"""

from dataclasses import dataclass
from pathlib import Path
import re

from controller.app.projects.config import EffectivePolicy, matches_any
from controller.app.projects.repo import COMMITTER, _git
from controller.app.schemas import PlanTask

# Dependencies and caches that setup or test runs create; never part of a role's change, never cleaned away.
GENERATED = ("node_modules", ".venv", "venv", "__pycache__", ".pytest_cache", ".npm")
SKIP_MARKER = re.compile(r"\.(?:skip|only|todo)\s*\(|\b(?:xit|xdescribe|xtest)\s*\(|\{\s*skip\s*:\s*true|"
                         r"@pytest\.mark\.(?:skip|xfail)|@unittest\.skip|@Disabled\b|t\.Skip\(")


@dataclass(frozen=True)
class Change:
    path: str
    status: str  # A, M, D or T (type change)
    added: int
    deleted: int


def stage_changes(worktree: Path) -> list[Change]:
    """Stage everything the session changed (ignored files stay out) and describe it."""
    _git("add", "-A", "--", ".", *(f":(glob,exclude)**/{name}/**" for name in GENERATED), cwd=worktree)
    statuses = {}
    for line in _git("diff", "--cached", "--name-status", "--no-renames", cwd=worktree).splitlines():
        status, path = line.split("\t", 1)
        statuses[path] = status[0]
    changes = []
    for line in _git("diff", "--cached", "--numstat", "--no-renames", cwd=worktree).splitlines():
        added, deleted, path = line.split("\t", 2)
        changes.append(Change(path, statuses.get(path, "M"), int(added) if added != "-" else 0,
                              int(deleted) if deleted != "-" else 0))
    return changes


def _added_lines(worktree: Path, path: str) -> list[str]:
    diff = _git("diff", "--cached", "-U0", "--", path, cwd=worktree)
    return [line[1:] for line in diff.splitlines() if line.startswith("+") and not line.startswith("+++")]


def _common(worktree: Path, change: Change, policy: EffectivePolicy) -> list[str]:
    problems = []
    if matches_any(change.path, policy.protected_paths):
        problems.append(f"touches protected path {change.path}")
    if (worktree / change.path).is_symlink():
        problems.append(f"adds a symlink at {change.path}")
    return problems


def check_tests(worktree: Path, changes: list[Change], policy: EffectivePolicy, *, allow_edits: bool = False) -> list[str]:
    """QA Engineer: only add test files or add cases; never change sources or weaken existing tests.

    allow_edits: a human ruled on a test dispute, so existing lines may change (still no deletions or skips).
    """
    problems = []
    for change in changes:
        problems += _common(worktree, change, policy)
        if not matches_any(change.path, policy.test_paths):
            problems.append(f"changes {change.path}, outside the test paths {policy.test_paths}")
            continue
        if change.status == "D":
            problems.append(f"deletes test file {change.path}")
        elif change.deleted and not allow_edits:
            problems.append(f"changes or removes {change.deleted} existing line(s) in {change.path}; tests may only be added")
        if change.status != "D" and any(SKIP_MARKER.search(line) for line in _added_lines(worktree, change.path)):
            problems.append(f"adds a skipped or focused test in {change.path}")
    if not changes:
        problems.append("wrote no test")
    return problems


def check_code(worktree: Path, changes: list[Change], task: PlanTask, policy: EffectivePolicy) -> list[str]:
    """Developer: only the task's files, inside the allowed paths, never the tests."""
    planned = set(task.modify) | set(task.create)
    problems = []
    for change in changes:
        problems += _common(worktree, change, policy)
        if matches_any(change.path, policy.test_paths):
            problems.append(f"edits test file {change.path}; report a test dispute instead")
        elif change.path not in planned:
            problems.append(f"changes {change.path}, which is not in task {task.id} ({', '.join(sorted(planned))})")
        elif not matches_any(change.path, policy.allowed_paths):
            problems.append(f"changes {change.path}, outside the allowed paths {policy.allowed_paths}")
        if change.status == "D":
            problems.append(f"deletes {change.path}")
    if not changes:
        problems.append("changed no file")
    return problems


def commit(worktree: Path, message: str, role: str) -> str:
    _git(*COMMITTER, "commit", "--quiet", "--no-verify", "-m", f"{message}\n\nSDLC-Role: {role}", cwd=worktree)
    return _git("rev-parse", "HEAD", cwd=worktree).strip()


def discard(worktree: Path) -> None:
    """Drop everything a rejected session left behind; ignored files such as dependencies stay."""
    _git("reset", "--quiet", "--hard", "HEAD", cwd=worktree)
    _git("clean", "-fdq", *(arg for name in GENERATED for arg in ("-e", name)), cwd=worktree)


def drop_commit(worktree: Path, subject_prefix: str) -> bool:
    """Undo the last commit if its subject starts with the prefix (rejected tests are rewritten, not appended)."""
    subject = _git("log", "-1", "--format=%s", cwd=worktree).strip()
    if not subject.startswith(subject_prefix):
        return False
    _git("reset", "--quiet", "--hard", "HEAD~1", cwd=worktree)
    return True


def find_commit(worktree: Path, base: str, subject_prefix: str) -> str | None:
    """The newest commit since base whose subject starts with the prefix."""
    for line in _git("log", "--format=%H %s", f"{base}..HEAD", cwd=worktree).splitlines():
        sha, _, subject = line.partition(" ")
        if subject.startswith(subject_prefix):
            return sha
    return None


def head(worktree: Path) -> str:
    return _git("rev-parse", "HEAD", cwd=worktree).strip()
