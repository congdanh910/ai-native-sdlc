"""When a provider's subscription window is back."""

from datetime import datetime, timezone

from controller.app.providers import provider_of, reset_time


def test_the_reset_time_a_provider_gives_is_read_in_local_time():
    now = datetime(2026, 10, 23, 12, 0, tzinfo=timezone.utc)
    at = reset_time("You've hit your usage limit ... try again at 7:44 PM.", now).astimezone()
    assert (at.hour, at.minute) == (19, 44) and at > now
    claude = reset_time("You've hit your session limit · resets 7:40pm (Asia/Saigon)", now).astimezone()
    assert (claude.hour, claude.minute) == (19, 40)
    assert reset_time("limit reached", now) > now  # no time given: subscription_wait_seconds later
    assert provider_of("codex-readonly") == "codex" and provider_of("claude") == "claude"


def test_the_chosen_model_and_effort_reach_the_cli_commands(tmp_path):
    from pathlib import Path
    from controller.app.doing.executors import ClaudeExecutor, CodexExecutor, Session
    from controller.app.team.readonly import CliReasoningClient
    session = Session(role="developer", prompt="p", worktree=tmp_path, writable=[], protected=[], commands=[],
                      timeout=10, log_path=tmp_path / "l.log", model="gpt-6-sol", effort="high")
    codex = CodexExecutor().args(session, tmp_path / "last.txt")
    assert codex[codex.index("-m") + 1] == "gpt-6-sol" and 'model_reasoning_effort="high"' in codex
    claude = ClaudeExecutor().args(Session(**{**session.__dict__, "model": "sonnet", "effort": ""}))
    assert claude[claude.index("--model") + 1] == "sonnet"
    plain = CodexExecutor().args(Session(**{**session.__dict__, "model": "", "effort": ""}), tmp_path / "last.txt")
    assert "-m" not in plain and not any("model_reasoning_effort" in a for a in plain)
    client = CliReasoningClient("claude")
    client.model = "opus"
    assert client.args(str(tmp_path), Path(tmp_path / "x"))[client.args(str(tmp_path), Path(tmp_path / "x")).index("--model") + 1] == "opus"
