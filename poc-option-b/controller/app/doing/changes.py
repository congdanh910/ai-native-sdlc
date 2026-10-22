"""Path and diff checks after each doing role, and the commits the controller makes for them.

The CLIs never commit or push. After a session the controller stages what
changed in the run worktree, checks it against the role's rules, and either
commits it under the role's name or discards it. These checks are authoritative;
the in-session guards only stop mistakes earlier.
"""

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import re

from controller.app.projects.config import EffectivePolicy, matches_any
from controller.app.projects.repo import COMMITTER, GitError, _git
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


def _removed_lines(worktree: Path, path: str) -> list[str]:
    diff = _git("diff", "--cached", "-U0", "--", path, cwd=worktree)
    return [line[1:] for line in diff.splitlines() if line.startswith("-") and not line.startswith("---")]


IMPORT_LINE = re.compile(r"^\s*(?:from\s+(?P<pymod>[\w.]+)\s+import\s+\(?(?P<pynames>[^)#]*)\)?|"
                         r"import\s*\{(?P<jsnames>[^}]*)\}\s*from\s*['\"](?P<jsmod>[^'\"]+)['\"])\s*;?\s*$")


def _import(line: str) -> tuple[str, set[str]] | None:
    match = IMPORT_LINE.match(line)
    if not match:
        return None
    module = match["pymod"] or match["jsmod"]
    names = {n.strip() for n in (match["pynames"] or match["jsnames"] or "").split(",") if n.strip()}
    return module, names


IMPORT_BLOCK = re.compile(r"^\s*from\s+[\w.]+\s+import\s*\((?P<py>[^)]*)\)|^\s*import\s*\{(?P<js>[^}]*)\}\s*from",
                          re.MULTILINE)
NAMES_LINE = re.compile(r"^\s*[A-Za-z_$][\w$]*(?:\s+as\s+\w+)?(?:\s*,\s*[A-Za-z_$][\w$]*(?:\s+as\s+\w+)?)*\s*,?\s*$")


def _names(line: str) -> set[str]:
    return {n.strip() for n in line.split(",") if n.strip()}


def _widened_imports(worktree: Path, path: str) -> bool:
    """Every removed line is an import that an added line imports again with at least the same names: a one-line
    import, or a line of names inside a multi-line one (from conftest import (\n a, b,\n))."""
    added_lines = _added_lines(worktree, path)
    added = [parsed for line in added_lines if (parsed := _import(line))]
    try:
        before = _git("show", f"HEAD:{path}", cwd=worktree)
    except GitError:  # a new file has no removed lines
        before = ""
    in_blocks = {line.strip() for block in IMPORT_BLOCK.finditer(before)
                 for line in (block["py"] or block["js"] or "").splitlines() if line.strip()}
    for line in _removed_lines(worktree, path):
        old = _import(line)
        if old is not None:
            if not any(module == old[0] and old[1] <= names for module, names in added):
                return False
        elif not (line.strip() in in_blocks and NAMES_LINE.match(line)
                  and any(NAMES_LINE.match(a) and _names(line) <= _names(a) for a in added_lines)):
            return False
    return True


def _common(worktree: Path, change: Change, policy: EffectivePolicy) -> list[str]:
    problems = []
    if matches_any(change.path, policy.protected_paths):
        problems.append(f"touches protected path {change.path}")
    if (worktree / change.path).is_symlink():
        problems.append(f"adds a symlink at {change.path}")
    return problems


def check_tests(worktree: Path, changes: list[Change], policy: EffectivePolicy, *, allow_edits: bool = False,
                allow_deletes: bool = False) -> list[str]:
    """QA Engineer: only add test files or add cases; never change sources or weaken existing tests.

    allow_edits: a human ruled on a test dispute, so existing lines may change (still no deletions or skips).
    allow_deletes: an acceptance suite written for an earlier preview is replaced, so its files may go.
    """
    problems = []
    for change in changes:
        problems += _common(worktree, change, policy)
        if not matches_any(change.path, policy.test_paths):
            problems.append(f"changes {change.path}, outside the test paths {policy.test_paths}")
            continue
        if change.status == "D" and not allow_deletes:
            problems.append(f"deletes test file {change.path}")
        elif change.deleted and not allow_edits and not _widened_imports(worktree, change.path):
            shown = "; ".join(repr(line.strip()[:120]) for line in _removed_lines(worktree, change.path)[:3])
            problems.append(f"changes or removes {change.deleted} existing line(s) in {change.path} ({shown}); "
                            "tests may only be added")
        if change.status != "D" and any(SKIP_MARKER.search(line) for line in _added_lines(worktree, change.path)):
            problems.append(f"adds a skipped or focused test in {change.path}")
    if not changes:
        problems.append("wrote no test")
    return problems


def check_code(worktree: Path, changes: list[Change], task: PlanTask, policy: EffectivePolicy) -> list[str]:
    """Developer: only the task's files, inside the allowed paths, never the tests."""
    planned = set(task.modify) | set(task.create)
    # a glob (a fix task: any file of the allowed paths, a new migration or a module an earlier plan wrote) admits
    # whatever matches it
    globs = [p for p in task.modify + task.create if any(ch in p for ch in "*?[")]
    problems = []
    for change in changes:
        problems += _common(worktree, change, policy)
        if matches_any(change.path, policy.test_paths):
            problems.append(f"edits test file {change.path}; report a test dispute instead")
        elif change.path not in planned and not matches_any(change.path, globs):
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


def drop_paths(worktree: Path, staged: list[Change], paths: list[str]) -> list[Change]:
    """Take these paths out of the staged change (new files removed, changed ones restored); returns what stays."""
    for change in staged:
        if change.path in paths:
            _git("reset", "--quiet", "HEAD", "--", change.path, cwd=worktree)
            if change.status == "A":
                (worktree / change.path).unlink(missing_ok=True)
            else:
                _git("checkout", "--quiet", "HEAD", "--", change.path, cwd=worktree)
    return [c for c in staged if c.path not in paths]


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


def run_diff(worktree: Path, base: str, test_paths: list[str], budget: int) -> str:
    """The run's code change for the reviewer: source files first, then tests, within a character budget.

    The artifact thread (.sdlc/) is left out; it is reviewed as documents, not as code.
    """
    files = [f for f in _git("diff", "--name-only", f"{base}..HEAD", cwd=worktree).splitlines()
             if not f.startswith(".sdlc/")]
    sources = [f for f in files if not matches_any(f, test_paths)]
    tests = [f for f in files if matches_any(f, test_paths)]
    parts, used = [], 0
    for group in (sources, tests):
        if not group:
            continue
        text = _git("diff", f"{base}..HEAD", "--", *group, cwd=worktree)
        room = budget - used
        if room < 200:
            parts.append(f"(diff of {', '.join(group)} left out: context budget)")
            break
        if len(text) > room:
            text = text[: room - 60] + "\n… diff truncated by the context budget …\n"
        parts.append(text)
        used += len(text)
    return "".join(parts)


TEST_NAME = re.compile(r"^\+\s*(?:async\s+)?def\s+(test\w*)|^\+\s*(?:it|test)\s*\(\s*['\"`]([^'\"`]+)")


def test_names(worktree: Path, commit: str) -> list[tuple[str, str]]:
    """(file, test name) of each test a commit adds: Python def test_..., and it(...) or test(...) titles."""
    names, path = [], ""
    for line in _git("show", "--format=", "-U0", commit, cwd=worktree).splitlines():
        if line.startswith("+++ "):
            path = line.removeprefix("+++ b/")
        elif (match := TEST_NAME.match(line)):
            names.append((path, match[1] or match[2]))
    return names


def commits_touching(worktree: Path, base: str, until: str, paths: list[str]) -> list[dict]:
    """The commits between base and until that changed any of these paths, newest first, with the paths."""
    if not paths:
        return []
    out = _git("log", "--format=%x00%H%x09%s", "--name-only", f"{base}..{until}", "--", *paths, cwd=worktree)
    commits = []
    for block in out.split("\0")[1:]:
        head, *files = block.strip().splitlines()
        sha, _, subject = head.partition("\t")
        commits.append({"sha": sha, "subject": subject, "files": [f for f in files if f]})
    return commits


def files_of(worktree: Path, commit: str) -> list[str]:
    """The paths a commit changed."""
    return [line for line in _git("show", "--format=", "--name-only", commit, cwd=worktree).splitlines() if line]


def changed_since(worktree: Path, base: str) -> list[str]:
    """Paths the commits since base changed."""
    return [line for line in _git("diff", "--name-only", "--no-renames", base, "HEAD", cwd=worktree).splitlines() if line]


def head(worktree: Path) -> str:
    return _git("rev-parse", "HEAD", cwd=worktree).strip()


def head_time(worktree: Path) -> datetime:
    return datetime.fromisoformat(_git("log", "-1", "--format=%cI", cwd=worktree).strip())
