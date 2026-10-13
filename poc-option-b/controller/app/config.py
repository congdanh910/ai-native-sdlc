from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str
    controller_token: str
    allowed_repos: str
    langflow_url: str = "http://127.0.0.1:7860"
    langflow_api_key: str = ""
    langflow_requirements_flow_id: str = ""
    langflow_planning_flow_id: str = ""
    langflow_review_flow_id: str = ""
    github_webhook_secret: str = ""
    github_required_check: str = "task-board-tests"
    gx10_ssh_host: str = ""
    gx10_ssh_port: int = 2222
    gx10_ssh_identity_file: str = ""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @property
    def repo_allowlist(self) -> frozenset[str]:
        return frozenset(repo.strip() for repo in self.allowed_repos.split(",") if repo.strip())


@lru_cache
def get_settings() -> Settings:
    return Settings()
