"""Settings loaded from poc-option-b/.env and the process environment."""

from functools import lru_cache
from pathlib import Path
import re

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = PROJECT_ROOT / ".env"
DATA_DIR = PROJECT_ROOT / ".data"
PLACEHOLDER = "CHANGE_ME"

SSH_DESTINATION = re.compile(r"[A-Za-z0-9_.-]+@[A-Za-z0-9_.:-]+")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ENV_FILE, extra="ignore")

    sdlc_operator: str = PLACEHOLDER

    sdlc_db_password: str = PLACEHOLDER
    sdlc_db_port: int = 55442
    database_url_override: str | None = Field(default=None, alias="DATABASE_URL")

    langflow_api_key: str = PLACEHOLDER
    langflow_port: int = 7870

    sdlc_api_port: int = 8770
    sdlc_team_mode: str = "mock"  # thinking roles: mock or qwen3
    sdlc_doing_mode: str = "mock"  # QA Engineer, Developer, Validation Gate: mock or cli (B4)
    github_webhook_secret: str = ""  # empty: no webhook; the worker polls GitHub every minute
    # Engines of normal runs per thinking role, e.g. "tech_lead=claude-readonly,qa_analyst=claude-readonly".
    # Roles not named use qwen3. Operator approval of 2026-10-14 covers Tech Lead and QA Analyst on claude-readonly.
    sdlc_engines: str = ""

    @property
    def default_engines(self) -> dict:
        from controller.app.team.readonly import validate_engines
        pairs = [item.split("=", 1) for item in self.sdlc_engines.replace(" ", "").split(",") if item]
        if any(len(pair) != 2 for pair in pairs):
            raise ValueError("SDLC_ENGINES must look like tech_lead=claude-readonly,qa_analyst=claude-readonly")
        return validate_engines(dict(pairs))

    ollama_tunnel_port: int = 11435
    ollama_remote_port: int = 11437
    ollama_model: str = "qwen3:30b-instruct"
    ollama_num_ctx: int = 8192
    context_char_budget: int = 16000

    gx10_ssh_host: str = PLACEHOLDER
    gx10_ssh_port: int = 22
    gx10_ssh_identity_file: str = PLACEHOLDER
    gx10_min_available_mib: int = 65536
    gx10_max_gpu_util: int = Field(default=20, ge=0, le=100)

    @field_validator("sdlc_db_port", "langflow_port", "sdlc_api_port", "ollama_tunnel_port",
                     "ollama_remote_port", "gx10_ssh_port")
    @classmethod
    def _valid_port(cls, value: int) -> int:
        if not 1 <= value <= 65535:
            raise ValueError("port must be 1..65535")
        return value

    @property
    def coding_is_mocked(self) -> bool:
        """The QA Engineer, Developer and Validation Gate are mocked unless SDLC_DOING_MODE=cli."""
        return self.sdlc_doing_mode != "cli"

    @property
    def database_url(self) -> str:
        if self.database_url_override:
            return self.database_url_override
        return f"postgresql+psycopg://sdlc:{self.sdlc_db_password}@127.0.0.1:{self.sdlc_db_port}/sdlc"

    @property
    def langflow_url(self) -> str:
        return f"http://127.0.0.1:{self.langflow_port}"

    @property
    def api_url(self) -> str:
        return f"http://127.0.0.1:{self.sdlc_api_port}"

    @property
    def ollama_url(self) -> str:
        return f"http://127.0.0.1:{self.ollama_tunnel_port}"

    @property
    def ollama_url_from_langflow(self) -> str:
        return f"http://host.docker.internal:{self.ollama_tunnel_port}"

    def placeholders(self) -> list[str]:
        """Names of settings that still hold a CHANGE_ME value."""
        return sorted(
            name.upper() for name, value in self.model_dump(by_alias=False).items()
            if isinstance(value, str) and PLACEHOLDER in value
        )

    def gx10_problems(self) -> list[str]:
        problems = []
        if not SSH_DESTINATION.fullmatch(self.gx10_ssh_host):
            problems.append("GX10_SSH_HOST must look like user@host")
        if not Path(self.gx10_ssh_identity_file).expanduser().is_file():
            problems.append("GX10_SSH_IDENTITY_FILE does not point to a file")
        return problems


@lru_cache
def get_settings() -> Settings:
    return Settings()
