from controller.app.config import get_settings
from controller.app.team.base import Team


def get_team() -> Team:
    """`mock`: every role is deterministic. `qwen3`: thinking roles on Qwen3 via Langflow, doing roles mocked (B4)."""
    settings = get_settings()
    if settings.sdlc_team_mode == "mock":
        from controller.app.team.mock import MockTeam
        return MockTeam()
    if settings.sdlc_team_mode == "qwen3":
        from controller.app.team.langflow_team import LangflowTeam
        return LangflowTeam(settings)
    raise ValueError(f"unknown SDLC_TEAM_MODE {settings.sdlc_team_mode!r}; use mock or qwen3")
