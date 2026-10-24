from controller.app.config import get_settings
from controller.app.team.base import Team


def get_team() -> Team:
    """Thinking roles by SDLC_TEAM_MODE (mock or qwen3), doing roles by SDLC_DOING_MODE (mock or cli)."""
    settings = get_settings()
    if settings.sdlc_team_mode == "mock":
        from controller.app.team.mock import MockTeam
        thinking = MockTeam()
    elif settings.sdlc_team_mode == "qwen3":
        if not settings.sdlc_qwen_enabled:
            raise ValueError("SDLC_TEAM_MODE=qwen3 needs SDLC_QWEN_ENABLED=true; use claude-readonly or codex-readonly")
        from controller.app.team.langflow_team import LangflowTeam
        thinking = LangflowTeam(settings)
    elif settings.sdlc_team_mode in ("claude-readonly", "codex-readonly"):
        from controller.app.team.readonly import ReadonlyTeam
        thinking = ReadonlyTeam(settings, settings.sdlc_team_mode.removesuffix("-readonly"))
    else:
        raise ValueError(f"unknown SDLC_TEAM_MODE {settings.sdlc_team_mode!r}; "
                         "use mock, qwen3, claude-readonly or codex-readonly")
    from controller.app.team.readonly import RoutingTeam
    thinking = RoutingTeam(thinking, settings)  # experiments may put a thinking role on a read-only CLI
    if settings.sdlc_doing_mode == "mock":
        return thinking
    if settings.sdlc_doing_mode == "cli":
        from controller.app.doing.team import CliDoing
        from controller.app.team.composite import CompositeTeam
        return CompositeTeam(thinking, CliDoing())
    raise ValueError(f"unknown SDLC_DOING_MODE {settings.sdlc_doing_mode!r}; use mock or cli")
