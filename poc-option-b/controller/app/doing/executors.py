"""Codex CLI and Claude Code CLI as the QA Engineer and the Developer.

Both run non-interactively under the operator's subscription login; no API key
is passed. Each call is a fresh session in the run worktree. Neither CLI may
commit or push: the controller checks and commits their changes afterwards.

In-session guards:
- Claude Code: `dontAsk` permission mode, file tools only (no shell: AI-written
  tests must not run on the host outside the validation container), and a
  PreToolUse hook (guard.py) that blocks edits outside the role's writable paths.
- Codex: `workspace-write` sandbox with network disabled. Codex has no per-path
  write rule here, so the controller's post-session checks are the only path guard.
"""

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import re
import shlex
import sys
import tempfile

from controller.app.doing.process import ProcessResult, run_bounded

GUARD = Path(__file__).with_name("guard.py")
DISPUTE_PREFIX = "DISPUTE:"
# How the CLIs report an exhausted subscription window; the job waits instead of failing.
LIMIT = re.compile(r"usage limit|rate limit|rate_limit|too many requests|\b429\b|quota exceeded|"
                   r"hit your (?:\w+ )?limit|(?:session|usage|weekly) limit|limit reached|resets \d", re.I)


@dataclass(frozen=True)
class Session:
    """What one role session may do."""
    role: str  # qa_engineer or developer
    prompt: str
    worktree: Path
    writable: list[str]  # path patterns the role may edit
    protected: list[str]  # path patterns it must never edit
    commands: list[list[str]]  # validation commands, named in the prompt; only Codex can run them (sandboxed)
    timeout: float
    log_path: Path


@dataclass(frozen=True)
class SessionResult:
    executor: str
    ok: bool
    message: str  # the session's final message
    seconds: float
    exit_code: int | None
    timed_out: bool
    usage: dict = field(default_factory=dict)

    @property
    def limited(self) -> bool:
        """The session failed because the subscription's usage window is exhausted."""
        return not self.ok and bool(LIMIT.search(self.message))

    @property
    def dispute(self) -> str | None:
        for line in self.message.splitlines():
            if line.strip().upper().startswith(DISPUTE_PREFIX):
                return line.strip()[len(DISPUTE_PREFIX):].strip() or "the Developer disputes a test"
        return None


def _base_env() -> dict[str, str]:
    """The operator's environment without API keys, so the CLIs use the subscription login."""
    env = {k: v for k, v in os.environ.items()
           if k not in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "CODEX_API_KEY", "ANTHROPIC_AUTH_TOKEN")}
    env["GIT_TERMINAL_PROMPT"] = "0"
    return env


def _write_log(path: Path, result: ProcessResult, extra: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"$ {shlex.join(result.args[:12])} …\nexit={result.exit_code} seconds={result.seconds}"
                    f"{' TIMED OUT' if result.timed_out else ''}\n{extra}\n\n{result.output}")


class ClaudeExecutor:
    name = "claude"

    def __init__(self, binary: str = "claude", runner=run_bounded):
        self.binary, self.runner = binary, runner

    def args(self, session: Session) -> list[str]:
        hook = f"{shlex.quote(sys.executable)} -I {shlex.quote(str(GUARD))}"
        settings = {"hooks": {"PreToolUse": [{"matcher": "Edit|MultiEdit|Write|NotebookEdit",
                                              "hooks": [{"type": "command", "command": hook}]}]}}
        tools = ["Read", "Glob", "Grep", "Edit", "MultiEdit", "Write"]
        return [self.binary, "-p", "--output-format", "stream-json", "--verbose", "--permission-mode", "dontAsk",
                "--no-session-persistence", "--setting-sources", "local", "--strict-mcp-config",
                "--tools", ",".join(tools), "--allowedTools", *tools, "--settings", json.dumps(settings)]

    def run(self, session: Session) -> SessionResult:
        env = _base_env() | {"SDLC_GUARD_ROOT": str(session.worktree), "SDLC_GUARD_ALLOW": json.dumps(session.writable),
                             "SDLC_GUARD_DENY": json.dumps(session.protected)}
        result = self.runner(self.args(session), cwd=session.worktree, timeout=session.timeout,
                             input_text=session.prompt, env=env)
        message, usage, is_error = result.output, {}, not result.ok
        try:
            data = json.loads(result.output.strip().splitlines()[-1])
            message = str(data.get("result") or "")
            is_error = is_error or bool(data.get("is_error"))
            usage = {"cost_usd": data.get("total_cost_usd"), "turns": data.get("num_turns"),
                     "duration_ms": data.get("duration_ms"), **(data.get("usage") or {})}
        except (ValueError, IndexError, AttributeError):
            pass
        _write_log(session.log_path, result)
        return SessionResult(self.name, not is_error, message, result.seconds, result.exit_code, result.timed_out,
                             usage)


class CodexExecutor:
    name = "codex"

    def __init__(self, binary: str = "codex", runner=run_bounded):
        self.binary, self.runner = binary, runner

    def args(self, session: Session, last_message: Path) -> list[str]:
        return [self.binary, "exec", "--sandbox", "workspace-write", "--cd", str(session.worktree), "--ephemeral",
                "-c", "sandbox_workspace_write.network_access=false", "-c", 'approval_policy="never"',
                "--json", "--output-last-message", str(last_message), "-"]

    def run(self, session: Session) -> SessionResult:
        with tempfile.TemporaryDirectory(prefix="sdlc-codex-") as tmp:
            last = Path(tmp) / "last-message.txt"
            result = self.runner(self.args(session, last), cwd=session.worktree, timeout=session.timeout,
                                 input_text=session.prompt, env=_base_env())
            message = last.read_text() if last.exists() else result.tail()
        usage = {}
        for line in result.output.splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if isinstance(event, dict) and isinstance(event.get("usage"), dict):
                usage = event["usage"]  # the last turn's totals
        _write_log(session.log_path, result)
        return SessionResult(self.name, result.ok, message, result.seconds, result.exit_code, result.timed_out, usage)


def executors() -> dict[str, object]:
    return {"claude": ClaudeExecutor(), "codex": CodexExecutor()}
