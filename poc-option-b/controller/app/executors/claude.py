"""Claude Code adapter with restricted file tools and no unattended Bash."""

from pathlib import Path
from typing import Callable

from controller.app.executors.process import ProcessResult, run_process


async def run_claude(
    worktree: Path, prompt: str, *, timeout_seconds: float = 1800,
    on_start: Callable[[int, int], None] | None = None,
) -> ProcessResult:
    return await run_process(
        ["claude", "-p", "--output-format", "json", "--restricted", "--strict-mcp-config",
         "--permission-mode", "acceptEdits", "--permission-prompts", "none",
         "--tools", "Read,Edit,Write,Glob,Grep"],
        worktree, timeout_seconds=timeout_seconds, input_text=prompt, on_start=on_start,
    )
