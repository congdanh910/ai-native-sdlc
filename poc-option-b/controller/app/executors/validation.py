"""Validate changed paths and execute only operator-approved commands."""

import re
import shlex
from pathlib import Path, PurePosixPath
from typing import Sequence

from controller.app.executors.process import ProcessResult, run_process


class ValidationError(RuntimeError):
    pass


PROTECTED_NAMES = {"AGENTS.md", "package.json", "package-lock.json", "Dockerfile", "docker-compose.yml"}
PROTECTED_DIRS = {".git", ".github", ".aws", ".ssh", "deploy", "deployment", "infra"}


def check_path(path: str, allowed_paths: Sequence[str], worktree: Path) -> None:
    parsed = PurePosixPath(path)
    if not path or parsed.is_absolute() or ".." in parsed.parts or "\\" in path or "\x00" in path:
        raise ValidationError(f"unsafe path: {path!r}")
    if parsed.name in PROTECTED_NAMES or parsed.name.startswith(".env") or any(
        part in PROTECTED_DIRS for part in parsed.parts
    ):
        raise ValidationError(f"protected path: {path}")
    if any(
        not allowed or PurePosixPath(allowed).is_absolute() or ".." in PurePosixPath(allowed).parts
        or "\\" in allowed or "\x00" in allowed
        for allowed in allowed_paths
    ):
        raise ValidationError("invalid task path allowlist")
    if not any(path == allowed or path.startswith(f"{allowed.rstrip('/')}/") for allowed in allowed_paths):
        raise ValidationError(f"path outside task allowlist: {path}")
    current = worktree
    for part in parsed.parts:
        current = current / part
        if current.is_symlink():
            raise ValidationError(f"symlink path: {path}")


async def changed_paths(worktree: Path, base_commit: str, allowed_paths: Sequence[str]) -> list[str]:
    paths: set[str] = set()
    for argv in (
        ["git", "diff", "--name-only", "-z", base_commit, "--"],
        ["git", "ls-files", "--others", "--exclude-standard", "-z"],
        ["git", "ls-files", "--others", "--ignored", "--exclude-standard", "-z"],
    ):
        result = await run_process(argv, worktree, timeout_seconds=15)
        if result.returncode or result.stdout_truncated:
            raise ValidationError("cannot enumerate all changed paths")
        paths.update(item for item in result.stdout.split("\x00") if item)
    for path in paths:
        check_path(path, allowed_paths, worktree)
    return sorted(paths)


async def run_validation(
    worktree: Path, base_commit: str, allowed_paths: Sequence[str],
    commands: Sequence[str], *, command_allowlist: Sequence[str],
    executable_allowlist: Sequence[str] = ("npm", "node"),
    timeout_seconds: float = 120,
    container_image: str | None = None,
) -> list[tuple[str, ProcessResult]]:
    if container_image is not None and not re.fullmatch(r"node@sha256:[0-9a-f]{64}", container_image):
        raise ValidationError("validation container image must be a pinned Node digest")
    await changed_paths(worktree, base_commit, allowed_paths)
    results: list[tuple[str, ProcessResult]] = []
    for command in commands:
        if command not in command_allowlist:
            raise ValidationError(f"command is not allowlisted: {command}")
        argv = shlex.split(command)
        if not argv or argv[0] not in executable_allowlist or re.search(r"[;&|`$<>]", command):
            raise ValidationError(f"unsafe validation command: {command}")
        process_argv = argv
        if container_image is not None:
            process_argv = [
                "docker", "run", "--rm", "--network", "none", "--read-only",
                "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
                "--pids-limit", "128", "--memory", "512m", "--cpus", "1",
                "--user", "65534:65534", "--tmpfs", "/tmp:rw,noexec,nosuid,size=16m",
                "-e", "HOME=/tmp", "-v", f"{worktree.resolve()}:/workspace:ro",
                "-w", "/workspace", container_image, *argv,
            ]
        result = await run_process(process_argv, worktree, timeout_seconds=timeout_seconds)
        results.append((command, result))
        if result.returncode:
            break
    await changed_paths(worktree, base_commit, allowed_paths)
    return results
