from controller.app.config import get_settings
from controller.app.team.base import Team


def get_team() -> Team:
    mode = get_settings().sdlc_team_mode
    if mode == "mock":
        from controller.app.team.mock import MockTeam
        return MockTeam()
    raise ValueError(f"unknown SDLC_TEAM_MODE {mode!r}; B2 supports only 'mock'")
