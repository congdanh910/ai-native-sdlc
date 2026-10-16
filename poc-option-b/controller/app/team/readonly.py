"""Thinking roles on a subscription CLI in read-only mode (`claude-readonly`, `codex-readonly`), for experiments.

The CLI gets the same role prompt and the same JSON input as Qwen3, may read the
run's worktree to check names, and writes nothing. Its answer goes through the
same normalisation, contract validation, retries and grounding check, so engines
can be compared on the same eval suite. Approved by the operator on 2026-10-13
for eval runs and experiment runs; on 2026-10-14, after the engine comparison,
also for the Tech Lead and QA Analyst of normal runs (SDLC_ENGINES).
"""

import json
from pathlib import Path
import shlex
import tempfile

from controller.app.config import Settings
from controller.app.doing.executors import LIMIT, _base_env
from controller.app.doing.process import run_bounded
from controller.app.integrations.langflow import FLOW_PREFIX, LangflowError, role_prompt
from controller.app.team.base import CallRecord, RunContext, SubscriptionLimit
from controller.app.team.langflow_team import LangflowTeam

ENGINES = ("qwen3", "claude-readonly", "codex-readonly")
THINKING_ROLES = ("business_analyst", "tech_lead", "qa_analyst", "code_reviewer")
READONLY_NOTE = ("You may read the repository in the current directory to check file names, functions and tests. "
                 "Do not change, create or run anything. The input JSON follows.")


class CliReasoningClient:
    """Stands in for the Langflow client: the same endpoint and payload, answered by a read-only CLI session."""

    def __init__(self, cli: str, runner=run_bounded, timeout: float = 600):
        self.cli, self.runner = cli, runner
        self.cwd: str | None = None
        self.timeout = timeout  # set per call from the project's limits.thinking_timeout_minutes
        self.last_usage: dict = {}

    def args(self, cwd: str, last_message: Path) -> list[str]:
        if self.cli == "claude":
            tools = ["Read", "Glob", "Grep"]
            return ["claude", "-p", "--output-format", "stream-json", "--verbose", "--permission-mode", "dontAsk",
                    "--no-session-persistence", "--setting-sources", "local", "--strict-mcp-config",
                    "--tools", ",".join(tools), "--allowedTools", *tools]
        return ["codex", "exec", "--sandbox", "read-only", "--cd", cwd, "--ephemeral",
                "-c", 'approval_policy="never"', "--json", "--output-last-message", str(last_message), "-"]

    def run(self, endpoint: str, payload: dict, *, session_id: str) -> str:
        role = endpoint.removeprefix(FLOW_PREFIX)
        prompt = f"{role_prompt(role).text}\n{READONLY_NOTE}\n\n{json.dumps(payload, indent=1)}\n"
        with tempfile.TemporaryDirectory(prefix="sdlc-readonly-") as tmp:
            cwd = self.cwd if self.cwd and Path(self.cwd).is_dir() else tmp
            last = Path(tmp) / "last-message.txt"
            result = self.runner(self.args(cwd, last), cwd=Path(cwd), timeout=self.timeout, input_text=prompt,
                                 env=_base_env())
            message = last.read_text() if last.exists() else ""
        self.last_usage = {"seconds": result.seconds}
        if self.cli == "claude":
            try:
                data = json.loads(result.output.strip().splitlines()[-1])
                message = str(data.get("result") or "")
                self.last_usage.update({"cost_usd": data.get("total_cost_usd"), "turns": data.get("num_turns"),
                                        **(data.get("usage") or {})})
                failed = bool(data.get("is_error")) or not result.ok
            except (ValueError, IndexError, AttributeError):
                message, failed = result.output, True
        else:
            failed = not result.ok
            for line in result.output.splitlines():
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if isinstance(event, dict) and isinstance(event.get("usage"), dict):
                    self.last_usage.update(event["usage"])
        if failed:
            text = message or result.tail(5)
            if LIMIT.search(text):
                raise SubscriptionLimit(f"{self.cli} subscription limit reached: {text[-200:]}")
            raise LangflowError(f"{self.cli} read-only session failed: {text[-300:]}")
        return message


class ReadonlyTeam(LangflowTeam):
    """LangflowTeam's contracts and retries, with a read-only CLI in place of Langflow and Qwen3."""

    def __init__(self, settings: Settings, cli: str, client: CliReasoningClient | None = None):
        super().__init__(settings, client=client or CliReasoningClient(cli, timeout=settings.thinking_timeout_seconds),
                         check_capacity=lambda: [],
                         check_ollama=lambda: None)
        self.engine = f"{cli}-readonly"

    def _ensure_capacity(self) -> None:  # no GX10 involved
        return None

    def _ask(self, role, payload, contract, repair, session_id, retries=None):
        result = super()._ask(role, payload, contract, repair, session_id, retries=retries)
        call = self.last_call
        self.last_call = CallRecord(self.engine, call.prompt_version, call.attempts, usage=dict(self.client.last_usage))
        return result

    def _constitution(self, context: RunContext) -> str:
        return context.constitution_md or ""  # subscription CLIs have room for the whole file

    def _in(self, context: RunContext):
        self.client.cwd = context.worktree or None
        if hasattr(self.client, "timeout") and context.policy is not None:
            self.client.timeout = context.policy.thinking_timeout_seconds

    def specify(self, context):
        self._in(context)
        return super().specify(context)

    def write_srs(self, context):
        self._in(context)
        return super().write_srs(context)

    def write_sds(self, context):
        self._in(context)
        return super().write_sds(context)

    def plan(self, context):
        self._in(context)
        return super().plan(context)

    def design_tests(self, context, plan):
        self._in(context)
        return super().design_tests(context, plan)

    def review(self, context, plan, tests, diff="", validation=""):
        self._in(context)
        return super().review(context, plan, tests, diff=diff, validation=validation)


class RoutingTeam:
    """Picks the engine of each thinking role from the run (pinned at creation); everything else goes to the base."""

    def __init__(self, base, settings: Settings, factory=None):
        self.base, self.settings = base, settings
        self.factory = factory or (lambda cli: ReadonlyTeam(settings, cli))
        self._teams: dict[str, object] = {}
        self.last_call = None

    @property
    def engine(self) -> str:
        return self.base.engine

    def _for(self, context: RunContext, role: str):
        engine = (context.engines or {}).get(role, self.settings.sdlc_default_engine)
        if engine == "qwen3":
            return self.base
        if engine not in ENGINES:
            raise ValueError(f"unknown reasoning engine {engine!r}")
        cli = engine.removesuffix("-readonly")
        if cli not in self._teams:
            self._teams[cli] = self.factory(cli)
        return self._teams[cli]

    def _call(self, team, method: str, *args, **kwargs):
        try:
            return getattr(team, method)(*args, **kwargs)
        finally:
            self.last_call = getattr(team, "last_call", None)

    def draft_intent(self, *args, **kwargs):
        return self._call(self.base, "draft_intent", *args, **kwargs)

    def specify(self, context):
        return self._call(self._for(context, "business_analyst"), "specify", context)

    def write_srs(self, context):
        return self._call(self._for(context, "business_analyst"), "write_srs", context)

    def write_sds(self, context):  # the Architect reasons on the Tech Lead's engine
        return self._call(self._for(context, "tech_lead"), "write_sds", context)

    def plan(self, context):
        return self._call(self._for(context, "tech_lead"), "plan", context)

    def design_tests(self, context, plan):
        return self._call(self._for(context, "qa_analyst"), "design_tests", context, plan)

    def review(self, context, plan, tests, diff="", validation=""):
        return self._call(self._for(context, "code_reviewer"), "review", context, plan, tests, diff=diff,
                          validation=validation)

    def __getattr__(self, name):  # doing roles and anything else
        return getattr(self.base, name)


def validate_engines(engines: dict | None, default: str | None = None, qwen_enabled: bool | None = None) -> dict:
    """Every thinking role's engine; a role not named gets `default` (SDLC_DEFAULT_ENGINE). With Qwen disabled
    (SDLC_QWEN_ENABLED=false) no role may use qwen3."""
    if default is None or qwen_enabled is None:
        from controller.app.config import get_settings
        settings = get_settings()
        default = settings.sdlc_default_engine if default is None else default
        qwen_enabled = settings.sdlc_qwen_enabled if qwen_enabled is None else qwen_enabled
    engines = dict(engines or {})
    for role, engine in engines.items():
        if role not in THINKING_ROLES:
            raise ValueError(f"unknown thinking role {role!r}; use {', '.join(THINKING_ROLES)}")
        if engine not in ENGINES:
            raise ValueError(f"unknown engine {engine!r} for {role}; use {', '.join(ENGINES)}")
    if default not in ENGINES:
        raise ValueError(f"unknown SDLC_DEFAULT_ENGINE {default!r}; use {', '.join(ENGINES)}")
    resolved = {role: engines.get(role, default) for role in THINKING_ROLES}
    if not qwen_enabled and "qwen3" in resolved.values():
        roles = [role for role, engine in resolved.items() if engine == "qwen3"]
        raise ValueError(f"Qwen is disabled (SDLC_QWEN_ENABLED=false) but {', '.join(roles)} would use qwen3; "
                         "set SDLC_DEFAULT_ENGINE or SDLC_ENGINES to a read-only CLI")
    return resolved


def describe(engines: dict) -> str:
    return " ".join(shlex.quote(f"{role}={engine}") for role, engine in engines.items() if engine != "qwen3") or "qwen3"
