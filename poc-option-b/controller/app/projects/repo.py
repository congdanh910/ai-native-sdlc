"""Read-only access to a project's bare mirror, plus run worktrees for the artifact thread.

Every git call uses an argument array. Repository files are read from Git
objects at a pinned commit, never from a working directory, so symlinks in the
repository cannot point the controller at files outside it.
"""

from dataclasses import dataclass
from pathlib import Path, PurePosixPath
import re
import subprocess

SHA = re.compile(r"[0-9a-f]{40}")
BRANCH = re.compile(r"[A-Za-z0-9._/-]{1,200}")
REMOTE = re.compile(r"(https://[A-Za-z0-9.-]+/[A-Za-z0-9._/-]+|git@[A-Za-z0-9.-]+:[A-Za-z0-9._/-]+)")
COMMITTER = ["-c", "user.name=SDLC Team", "-c", "user.email=sdlc-team@localhost",
             "-c", "commit.gpgsign=false"]


class GitError(RuntimeError):
    pass


def _git(*args: str, cwd: Path | None = None, timeout: float = 120, input_text: str | None = None) -> str:
    result = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=timeout,
                            input=input_text, check=False)
    if result.returncode:
        message = (result.stderr or result.stdout).strip().splitlines()
        raise GitError(message[-1] if message else f"git {args[0]} failed")
    return result.stdout


def validate_repo_url(url: str) -> str:
    """Accept GitHub-style remotes or an absolute path to a local repository."""
    if REMOTE.fullmatch(url) and ".." not in url:
        return url
    path = Path(url.removeprefix("file://")).expanduser()
    if path.is_absolute() and ((path / ".git").exists() or (path / "HEAD").is_file()):
        return str(path)
    raise ValueError("repository must be https://host/owner/repo, git@host:owner/repo, or an absolute path to a git repository")


def safe_repo_path(path: str) -> str:
    pure = PurePosixPath(path)
    if not path or pure.is_absolute() or ".." in pure.parts or "\\" in path:
        raise ValueError(f"path escapes the repository: {path!r}")
    return str(pure)


@dataclass(frozen=True)
class Mirror:
    path: Path

    def clone(self, url: str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        _git("clone", "--bare", "--quiet", "--", url, str(self.path), timeout=600)

    def fetch(self) -> None:
        # Run branches (sdlc/*) belong to the platform: their worktrees hold the truth, and once pushed for a
        # draft PR git would refuse to update a branch that a run worktree has checked out.
        _git("--git-dir", str(self.path), "fetch", "--quiet", "--prune", "origin",
             "+refs/heads/*:refs/heads/*", "^refs/heads/sdlc/*", timeout=600)

    def default_branch(self) -> str:
        ref = _git("--git-dir", str(self.path), "symbolic-ref", "--short", "HEAD").strip()
        return ref or "main"

    def resolve(self, branch: str) -> str:
        if not BRANCH.fullmatch(branch) or branch.startswith("-"):
            raise ValueError(f"invalid branch name {branch!r}")
        sha = _git("--git-dir", str(self.path), "rev-parse", "--verify", f"refs/heads/{branch}^{{commit}}").strip()
        if not SHA.fullmatch(sha):
            raise GitError(f"cannot resolve branch {branch}")
        return sha

    def read_file(self, commit: str, path: str) -> str | None:
        """Content of a regular file at a commit; None if missing. Symlinks and submodules are refused."""
        if not SHA.fullmatch(commit):
            raise ValueError("commit must be a full SHA")
        path = safe_repo_path(path)
        listing = _git("--git-dir", str(self.path), "ls-tree", commit, "--", path).strip()
        if not listing:
            return None
        mode, kind, obj = listing.split("\t", 1)[0].split()
        if kind != "blob" or mode not in ("100644", "100755"):
            raise ValueError(f"{path} is not a regular file")
        return _git("--git-dir", str(self.path), "cat-file", "blob", obj)

    def list_files(self, commit: str) -> list[str]:
        if not SHA.fullmatch(commit):
            raise ValueError("commit must be a full SHA")
        return _git("--git-dir", str(self.path), "ls-tree", "-r", "--name-only", commit).splitlines()


@dataclass(frozen=True)
class Worktree:
    """Disposable working tree of one run, on its own branch from the base commit."""
    mirror: Mirror
    path: Path
    branch: str

    def create(self, base_commit: str) -> None:
        if self.path.exists():
            return  # idempotent: the run already has its worktree
        self.path.parent.mkdir(parents=True, exist_ok=True)
        _git("--git-dir", str(self.mirror.path), "worktree", "add", "--quiet", "-b", self.branch,
             str(self.path), base_commit)

    def read(self, relative_path: str) -> str | None:
        """A file as the run branch has it now; None if missing."""
        target = self.path / safe_repo_path(relative_path)
        return target.read_text() if target.is_file() and not target.is_symlink() else None

    def commit_files(self, files: dict[str, str | bytes], message: str) -> str:
        """Write several files (text, or bytes for a binary file) and commit them together; returns the commit SHA."""
        paths = []
        for relative_path, content in files.items():
            relative_path = safe_repo_path(relative_path)
            target = self.path / relative_path
            if target.is_symlink():
                raise ValueError(f"refusing to write through a symlink: {relative_path}")
            target.parent.mkdir(parents=True, exist_ok=True)
            if isinstance(content, bytes):
                target.write_bytes(content)
            else:
                target.write_text(content)
            paths.append(relative_path)
        _git("add", "--", *paths, cwd=self.path)
        if _git("status", "--porcelain", "--", *paths, cwd=self.path).strip():
            _git(*COMMITTER, "commit", "--quiet", "-m", message, "--", *paths, cwd=self.path)
        return _git("rev-parse", "HEAD", cwd=self.path).strip()

    def commit_file(self, relative_path: str, content: str, message: str) -> str:
        """Write one file and commit it; returns the commit SHA (unchanged content is not committed again)."""
        relative_path = safe_repo_path(relative_path)
        target = self.path / relative_path
        if target.is_symlink():
            raise ValueError(f"refusing to write through a symlink: {relative_path}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
        _git("add", "--", relative_path, cwd=self.path)
        if _git("status", "--porcelain", "--", relative_path, cwd=self.path).strip():
            _git(*COMMITTER, "commit", "--quiet", "-m", message, "--", relative_path, cwd=self.path)
        return _git("rev-parse", "HEAD", cwd=self.path).strip()

    def remove(self) -> None:
        if self.path.exists():
            _git("--git-dir", str(self.mirror.path), "worktree", "remove", "--force", str(self.path))
