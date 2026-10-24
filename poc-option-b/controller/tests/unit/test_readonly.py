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
    assert spec.acceptance_criteria[0].id == "AC-01"
    assert args[args.index("--tools") + 1] == "Read,Glob,Grep" and "Edit" not in args and cwd == tmp_path
    assert "Business Analyst" in prompt and "Do not change, create or run anything" in prompt
    assert team.last_call.engine == "claude-readonly" and team.last_call.usage["cost_usd"] == 0.05


def test_codex_readonly_uses_the_read_only_sandbox(settings):
    client = CliReasoningClient("codex")
    args = client.args("/tmp/x", tmp := __import__("pathlib").Path("/tmp/m"))
    assert "--sandbox" not in args and 'default_permissions="sdlc"' in args and args[-1] == "-"
    assert '":workspace_roots" = {"." = "read"}' in next(a for a in args if a.startswith("permissions.sdlc.filesystem="))


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
    defaults = {"default": "qwen3", "qwen_enabled": True}  # independent of the operator's .env
    assert validate_engines({"tech_lead": "claude-readonly"}, **defaults)["business_analyst"] == "qwen3"
    with pytest.raises(ValueError, match="unknown engine"):
        validate_engines({"tech_lead": "gpt"}, **defaults)
    with pytest.raises(ValueError, match="unknown thinking role"):
        validate_engines({"developer": "qwen3"}, **defaults)


def test_default_engines_come_from_settings(settings):
    assert settings.default_engines == {r: "qwen3" for r in ("business_analyst", "tech_lead", "qa_analyst",
                                                              "code_reviewer")}
    custom = settings.model_copy(update={"sdlc_engines": "tech_lead=claude-readonly, qa_analyst=claude-readonly"})
    assert custom.default_engines["tech_lead"] == "claude-readonly" and custom.default_engines["code_reviewer"] == "qwen3"
    with pytest.raises(ValueError):
        _ = settings.model_copy(update={"sdlc_engines": "tech_lead"}).default_engines


def test_without_qwen_every_role_needs_a_cli_engine():
    import pytest
    from controller.app.team.readonly import validate_engines
    assert validate_engines({"tech_lead": "codex-readonly"}, default="claude-readonly", qwen_enabled=False) == {
        "business_analyst": "claude-readonly", "tech_lead": "codex-readonly", "qa_analyst": "claude-readonly",
        "code_reviewer": "claude-readonly"}
    with pytest.raises(ValueError, match="Qwen is disabled.*code_reviewer"):
        validate_engines({"code_reviewer": "qwen3"}, default="claude-readonly", qwen_enabled=False)
    with pytest.raises(ValueError, match="business_analyst"):
        validate_engines({}, default="qwen3", qwen_enabled=False)


def test_a_claude_base_team_drafts_intents_without_langflow(monkeypatch):
    from controller.app import team as team_module
    from controller.app.config import Settings
    from controller.app.team.readonly import ReadonlyTeam
    settings = Settings(sdlc_team_mode="claude-readonly", sdlc_qwen_enabled=False, sdlc_default_engine="claude-readonly",
                        sdlc_doing_mode="mock")
    monkeypatch.setattr(team_module, "get_settings", lambda: settings)
    built = team_module.get_team()
    assert isinstance(built.base, ReadonlyTeam) and built.base.engine == "claude-readonly"


def test_health_skips_langflow_and_gx10_without_qwen(monkeypatch):
    from controller.app import health
    from controller.app.config import Settings
    settings = Settings(sdlc_qwen_enabled=False)
    monkeypatch.setattr(health, "check_postgres", lambda e: health.Check("postgres", health.OK, ""))
    monkeypatch.setattr(health, "check_migrations", lambda e: health.Check("migrations", health.OK, ""))
    monkeypatch.setattr(health, "check_worker", lambda: health.Check("worker", health.OK, ""))
    names = [c.name for c in health.readiness_checks(settings, None)]
    assert names == ["postgres", "migrations", "worker"]
