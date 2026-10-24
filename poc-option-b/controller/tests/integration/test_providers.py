"""AI providers: a thinking role moves to its next engine when one is out of quota; the Admin orders the engines."""

import pytest

from controller.app import providers
from controller.app.config import get_settings
from controller.app.team.base import RunContext, SubscriptionLimit
from controller.app.team.readonly import RoutingTeam

pytestmark = pytest.mark.integration


class Engine:
    def __init__(self, name, limited=False):
        self.name, self.limited, self.calls, self.last_call = name, limited, 0, None

    def specify(self, context):
        self.calls += 1
        if self.limited:
            raise SubscriptionLimit(f"{self.name} subscription limit reached: try again at 7:44 PM")
        return f"spec by {self.name}"


def context(**engines) -> RunContext:
    return RunContext(title="t", policy=None, agents_md="", review_md=None, intent_md="", files=[], engines=engines)


def routing(teams: dict) -> RoutingTeam:
    return RoutingTeam(Engine("base"), get_settings(), factory=lambda cli: teams[cli])


def test_a_thinking_role_moves_to_the_next_engine_and_the_provider_is_marked(db):
    teams = {"codex": Engine("codex", limited=True), "claude": Engine("claude")}
    team = routing(teams)
    assert team.specify(context(business_analyst="codex-readonly")) == "spec by claude"
    assert "codex" in providers.limits()
    assert team.specify(context(business_analyst="codex-readonly")) == "spec by claude"
    assert teams["codex"].calls == 1  # limited: not asked again until it is back
    providers.mark_available("codex", actor="t")
    teams["codex"].limited = False
    assert team.specify(context(business_analyst="codex-readonly")) == "spec by codex"


def test_the_admin_orders_the_engines_and_an_experiment_keeps_its_engine(db):
    teams = {"codex": Engine("codex"), "claude": Engine("claude")}
    providers.save({"thinking": {"business_analyst": ["claude-readonly", "codex-readonly"]}}, actor="t")
    assert routing(teams).specify(context(business_analyst="codex-readonly")) == "spec by claude"
    pinned = RunContext(title="t", policy=None, agents_md="", review_md=None, intent_md="", files=[],
                        engines={"business_analyst": "codex-readonly"}, experiment=True)
    assert routing(teams).specify(pinned) == "spec by codex"
    with pytest.raises(ValueError, match="at most once"):
        providers.save({"thinking": {"tech_lead": ["codex-readonly", "codex-readonly"]}}, actor="t")


def test_when_every_engine_is_out_of_quota_the_job_waits_until_the_first_is_back(db):
    teams = {"codex": Engine("codex", limited=True), "claude": Engine("claude", limited=True)}
    with pytest.raises(SubscriptionLimit) as raised:
        routing(teams).specify(context(business_analyst="codex-readonly"))
    assert raised.value.until is not None and raised.value.wait_seconds >= 60


def test_the_admin_chooses_a_model_per_role_and_provider(db):
    providers.save({"models": {"tech_lead": {"claude": {"model": "opus"}, "codex": {"model": "gpt-6-sol", "effort": "high"}},
                               "developer": {"codex": {"model": "gpt-6-sol", "effort": "medium"}}}}, actor="t")
    assert providers.model_for("tech_lead", "claude-readonly") == ("opus", "")
    assert providers.model_for("developer", "codex") == ("gpt-6-sol", "medium")
    assert providers.model_for("qa_analyst", "codex-readonly") == ("", "")
    with pytest.raises(ValueError, match="Codex only"):
        providers.save({"models": {"tech_lead": {"claude": {"model": "opus", "effort": "high"}}}}, actor="t")
    with pytest.raises(ValueError, match="not a model name"):
        providers.save({"models": {"tech_lead": {"codex": {"model": "gpt 6; rm -rf"}}}}, actor="t")


def test_codex_models_and_their_efforts_come_from_the_codex_cache(db, tmp_path, monkeypatch):
    import json
    cache = tmp_path / "models_cache.json"
    cache.write_text(json.dumps({"models": [
        {"slug": "gpt-6-sol", "display_name": "GPT-6-Sol", "visibility": "list", "default_reasoning_level": "medium",
         "supported_reasoning_levels": [{"effort": e} for e in ("low", "medium", "high", "xhigh", "max", "ultra")]},
        {"slug": "gpt-6-luna", "display_name": "GPT-6-Luna", "visibility": "list",
         "supported_reasoning_levels": [{"effort": e} for e in ("low", "medium", "high")]},
        {"slug": "hidden", "visibility": "hide", "supported_reasoning_levels": []}]}))
    monkeypatch.setattr(get_settings(), "sdlc_codex_models_cache", str(cache))
    assert [m["slug"] for m in providers.codex_models()] == ["gpt-6-sol", "gpt-6-luna"]
    assert providers.efforts() == ("", "low", "medium", "high", "xhigh", "max", "ultra")
    providers.save({"models": {"developer": {"codex": {"model": "gpt-6-sol", "effort": "ultra"}}}}, actor="t")
    with pytest.raises(ValueError, match="not one of low, medium, high"):
        providers.save({"models": {"developer": {"codex": {"model": "gpt-6-luna", "effort": "ultra"}}}}, actor="t")


def test_every_suggested_model_is_one_the_settings_accept(db):
    suggested = providers.recommendations()
    assert set(suggested) == set(providers.THINKING_ROLES + providers.DOING_ROLES)
    models = {role: {p: {"model": v[p]["model"], "effort": v[p].get("effort", "")} for p in ("codex", "claude") if p in v}
              for role, v in suggested.items()}
    assert providers.save({"models": models}, actor="t")["models"]["code_reviewer"]["claude"]["model"] == "opus"
