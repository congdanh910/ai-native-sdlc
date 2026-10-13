"""Codex CLI adapter; the worker supplies a pre-approved task prompt."""

from pathlib import Path
from typing import Callable

from controller.app.executors.process import ProcessResult, run_process


async def run_codex(
    worktree: Path, prompt: str, *, timeout_seconds: float = 1800,
    on_start: Callable[[int, int], None] | None = None,
) -> ProcessResult:
    return await run_process(
        [
            "codex", "--no-daemon", "-a", "never", "-s", "workspace-write",
            "-c", "sandbox_workspace_write.network_access=false",
            "-c", "web_search=disabled", "exec", "--ignore-user-config",
            "--ephemeral", "-C", str(worktree), "--json", "-",
        ],
        worktree, timeout_seconds=timeout_seconds, input_text=prompt, on_start=on_start,
    )
