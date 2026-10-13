"""Run child processes with bounded output and process-group cancellation."""

import asyncio
import os
import signal
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, Sequence


MAX_OUTPUT_BYTES = 64 * 1024


class ProcessError(RuntimeError):
    pass


@dataclass(frozen=True)
class ProcessResult:
    returncode: int
    stdout: str
    stderr: str
    stdout_truncated: bool
    stderr_truncated: bool


async def run_process(
    argv: Sequence[str], cwd: Path, *, timeout_seconds: float,
    input_text: str = "", env: Mapping[str, str] | None = None,
    secrets: Sequence[str] = (), on_start: Callable[[int, int], None] | None = None,
) -> ProcessResult:
    if not argv or any(not isinstance(arg, str) or "\x00" in arg for arg in argv):
        raise ValueError("invalid process arguments")
    if not cwd.is_dir() or timeout_seconds <= 0:
        raise ValueError("invalid working directory or timeout")
    if len(input_text.encode("utf-8")) > 100_000:
        raise ValueError("process input exceeds limit")
    process = await asyncio.create_subprocess_exec(
        *argv, cwd=str(cwd), env=dict(env) if env is not None else None,
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE, start_new_session=True,
    )

    async def drain(stream: asyncio.StreamReader) -> tuple[bytes, bool]:
        chunks = bytearray()
        truncated = False
        while chunk := await stream.read(8192):
            remaining = MAX_OUTPUT_BYTES - len(chunks)
            chunks.extend(chunk[:remaining])
            truncated |= len(chunk) > remaining
        return bytes(chunks), truncated

    stdout_task = asyncio.create_task(drain(process.stdout))
    stderr_task = asyncio.create_task(drain(process.stderr))
    async def finish() -> tuple[tuple[bytes, bool], tuple[bytes, bool]]:
        if input_text:
            process.stdin.write(input_text.encode("utf-8"))
            await process.stdin.drain()
        process.stdin.close()
        await process.wait()
        return await asyncio.shield(asyncio.gather(stdout_task, stderr_task))

    try:
        if on_start is not None:
            on_start(process.pid, process.pid)
        stdout, stderr = await asyncio.wait_for(finish(), timeout_seconds)
    except BaseException:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        await process.wait()
        await asyncio.gather(stdout_task, stderr_task)
        raise
    def clean(data: bytes) -> str:
        value = data.decode("utf-8", errors="replace")
        for secret in secrets:
            if secret:
                value = value.replace(secret, "[REDACTED]")
        return value

    return ProcessResult(process.returncode, clean(stdout[0]), clean(stderr[0]), stdout[1], stderr[1])
