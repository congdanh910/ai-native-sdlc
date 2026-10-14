import json

import pytest

from controller.app.doing.process import ProcessResult
from controller.app.team.base import SubscriptionLimit
from controller.app.team.mock import MockTeam
from controller.app.team.readonly import CliReasoningClient, ReadonlyTeam, RoutingTeam, validate_engines
from controller.tests.unit.test_langflow import SPEC, context


def claude_output(text, error=False):
    return json.dumps({"result": text, "is_error": error, "total_cost_usd": 0.05, "num_turns": 3,
                       "usage": {"output_tokens": 500}})


def test_claude_readonly_answers_the_same_contract(settings, tmp_path):
    seen = []

    def runner(args, cwd, timeout, input_text, env):
        seen.append((args, cwd, input_text))
        return ProcessResult(args, 0, claude_output(json.dumps(SPEC)), 4.0)
    team = ReadonlyTeam(settings, "claude", client=CliReasoningClient("claude", runner=runner))
    base = context()
    spec = team.specify(base.__class__(**{**base.__dict__, "worktree": str(tmp_path)}))
    args, cwd, prompt = seen[0]
    assert spec.acceptance_criteria[0].id == "AC-1"
    assert args[args.index("--tools") + 1] == "Read,Glob,Grep" and "Edit" not in args and cwd == tmp_path
    assert "Business Analyst" in prompt and "Do not change, create or run anything" in prompt
    assert team.last_call.engine == "claude-readonly" and team.last_call.usage["cost_usd"] == 0.05


def test_codex_readonly_uses_the_read_only_sandbox(settings):
    client = CliReasoningClient("codex")
    args = client.args("/tmp/x", tmp := __import__("pathlib").Path("/tmp/m"))
    assert args[args.index("--sandbox") + 1] == "read-only" and args[-1] == "-"


def test_invalid_answers_are_retried_and_limits_postpone(settings):
    answers = iter([claude_output("not json"), claude_output(json.dumps(SPEC))])
    team = ReadonlyTeam(settings, "claude", client=CliReasoningClient(
        "claude", runner=lambda args, cwd, timeout, input_text, env: ProcessResult(args, 0, next(answers), 1.0)))
    assert team.specify(context()).goal == "g" and team.last_call.attempts == 2
    limited = ReadonlyTeam(settings, "claude", client=CliReasoningClient(
        "claude", runner=lambda *a, **k: ProcessResult([], 1, claude_output("Claude AI usage limit reached", True), 1.0)))
    with pytest.raises(SubscriptionLimit):
        limited.specify(context())


def test_routing_uses_the_run_engines_and_qwen3_by_default(settings):
    class Recording(MockTeam):
        def __init__(self, name):
            self.engine = name
    routed = RoutingTeam(Recording("qwen3:30b"), settings, factory=lambda cli: Recording(f"{cli}-readonly"))
    plain = context()
    experiment = plain.__class__(**{**plain.__dict__, "engines": {"tech_lead": "codex-readonly"}})
    routed.specify(experiment)
    assert routed._for(experiment, "tech_lead").engine == "codex-readonly"
    assert routed._for(experiment, "business_analyst").engine == "qwen3:30b"
    assert routed._for(plain, "tech_lead").engine == "qwen3:30b"


def test_engine_choices_are_validated():
    assert validate_engines({"tech_lead": "claude-readonly"})["business_analyst"] == "qwen3"
    with pytest.raises(ValueError, match="unknown engine"):
        validate_engines({"tech_lead": "gpt"})
    with pytest.raises(ValueError, match="unknown thinking role"):
        validate_engines({"developer": "qwen3"})
