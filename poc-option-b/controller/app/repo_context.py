"""Read agent context from an immutable Git commit, never the working tree."""

import hashlib
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path


COMMIT_PATTERN = re.compile(r"^[0-9a-fA-F]{40}([0-9a-fA-F]{24})?$")
MAX_CONTEXT_BYTES = 100_000
MAX_FILE_LIST_BYTES = 20_000


class RepoContextError(Exception):
    pass


@dataclass(frozen=True)
class RepoContext:
    text: str
    sha256: str
    artifact_path: str


def load_agent_context(repo_path: Path, base_commit: str, expected_sha256: str | None = None) -> RepoContext:
    if not COMMIT_PATTERN.fullmatch(base_commit):
        raise RepoContextError("base commit must be a full Git hash")
    repo_path = repo_path.resolve()
    if not repo_path.is_dir():
        raise RepoContextError("repository path does not exist")
    object_name = f"{base_commit}:AGENTS.md"
    try:
        size_result = subprocess.run(
            ["git", "-C", str(repo_path), "cat-file", "-s", object_name],
            capture_output=True, text=True, check=True, timeout=10,
        )
        size = int(size_result.stdout.strip())
        if size > MAX_CONTEXT_BYTES:
            raise RepoContextError("AGENTS.md exceeds the size limit")
        result = subprocess.run(
            ["git", "-C", str(repo_path), "show", object_name],
            capture_output=True, check=True, timeout=10,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, ValueError) as exc:
        raise RepoContextError("AGENTS.md is missing or unreadable at the pinned commit") from exc
    if len(result.stdout) != size:
        raise RepoContextError("AGENTS.md size changed while reading")
    try:
        contents = result.stdout.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise RepoContextError("AGENTS.md is not UTF-8") from exc
    if not contents.strip():
        raise RepoContextError("AGENTS.md is empty")
    checksum = hashlib.sha256(result.stdout).hexdigest()
    if expected_sha256 is not None and checksum != expected_sha256:
        raise RepoContextError("AGENTS.md checksum does not match the pinned benchmark")
    return RepoContext(contents, checksum, f"git:{base_commit}:AGENTS.md")


def load_pinned_file_list(repo_path: Path, base_commit: str) -> str:
    """List tracked paths from the same immutable commit as the agent context."""
    if not COMMIT_PATTERN.fullmatch(base_commit):
        raise RepoContextError("base commit must be a full Git hash")
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_path.resolve()), "ls-tree", "-r", "--name-only", "-z", base_commit],
            capture_output=True, check=True, timeout=10,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise RepoContextError("file list is unreadable at the pinned commit") from exc
    if len(result.stdout) > MAX_FILE_LIST_BYTES:
        raise RepoContextError("pinned file list exceeds the size limit")
    try:
        paths = [path.decode("utf-8") for path in result.stdout.split(b"\0") if path]
    except UnicodeDecodeError as exc:
        raise RepoContextError("pinned file list is not UTF-8") from exc
    return "\n".join(paths)
