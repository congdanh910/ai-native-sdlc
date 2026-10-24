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
    sdlc_team_mode: str = "mock"  # base of the thinking roles: mock, qwen3, claude-readonly or codex-readonly
    # Qwen3 on GX10 through Langflow. Off: `sdlc up` starts neither Langflow nor the GX10 tunnel, the health checks
    # skip them, and no role may use qwen3.
    sdlc_qwen_enabled: bool = True
    sdlc_default_engine: str = "qwen3"  # engine of a thinking role that SDLC_ENGINES does not name
    sdlc_doing_mode: str = "mock"  # QA Engineer, Developer, Validation Gate: mock or cli (B4)
    # Claude account of the platform's sessions: its config dir (CLAUDE_CONFIG_DIR of the API and worker). Empty: the
    # default ~/.claude, never the CLAUDE_CONFIG_DIR of the shell that ran `sdlc up` (a developer's own account).
    sdlc_claude_config_dir: str = ""
    github_webhook_secret: str = ""  # empty: no webhook; the worker polls GitHub every minute
    # Engines of normal runs per thinking role, e.g. "tech_lead=claude-readonly,qa_analyst=claude-readonly".
    # Roles not named use qwen3. Operator approvals of 2026-10-14 cover the Business Analyst, Tech Lead and
    # QA Analyst on claude-readonly.
    sdlc_engines: str = ""

    @property
    def default_engines(self) -> dict:
        from controller.app.team.readonly import validate_engines
        pairs = [item.split("=", 1) for item in self.sdlc_engines.replace(" ", "").split(",") if item]
        if any(len(pair) != 2 for pair in pairs):
            raise ValueError("SDLC_ENGINES must look like tech_lead=claude-readonly,qa_analyst=claude-readonly")
        return validate_engines(dict(pairs), default=self.sdlc_default_engine, qwen_enabled=self.sdlc_qwen_enabled)

    ollama_tunnel_port: int = 11435
    ollama_remote_port: int = 11437
    ollama_model: str = "qwen3:30b-instruct"
    ollama_num_ctx: int = 8192
    context_char_budget: int = 16000
    constitution_char_budget: int = 6000  # Qwen3 roles; read-only CLIs get the whole file

    gx10_ssh_host: str = PLACEHOLDER
    gx10_ssh_port: int = 22
    gx10_ssh_identity_file: str = PLACEHOLDER
    gx10_min_available_mib: int = 65536
    gx10_max_gpu_util: int = Field(default=20, ge=0, le=100)
    tunnel_wait_seconds: int = Field(default=15, ge=1)  # how long `sdlc up` waits for the GX10 tunnel to open
    gx10_capacity_cache_seconds: int = Field(default=120, ge=0)  # how long a good GX10 capacity reading is trusted

    # Worker and integrations. Per-project limits (session timeouts, retries, container) are in .sdlc/project.yaml.
    job_lease_seconds: int = Field(default=120, ge=10)  # a claimed job is renewed while it runs; expiry frees it
    job_max_attempts: int = Field(default=3, ge=1, le=10)  # retries of a job without side effects
    # validation runs again this many times when its environment broke (a service down, a container gone, exit 137)
    # before the task stops; the Developer is never asked to fix that
    validation_infra_retries: int = Field(default=1, ge=0, le=5)
    # how much of the failing commands' logs the Developer and the Code Reviewer's brief read
    validation_failure_chars: int = Field(default=16000, ge=2000, le=100000)
    job_retry_base_seconds: int = Field(default=10, ge=1)  # back-off doubles on each retry
    capacity_wait_seconds: int = Field(default=120, ge=5)  # GX10 busy: wait this long, then try again
    subscription_wait_seconds: int = Field(default=900, ge=60)  # a CLI subscription window is used up
    cancel_check_seconds: int = Field(default=5, ge=1)  # how often a running job checks for a cancellation
    pr_poll_seconds: int = Field(default=60, ge=10)  # GitHub CI, review and merge polling without a webhook
    gh_timeout_seconds: int = Field(default=60, ge=5)
    git_push_timeout_seconds: int = Field(default=300, ge=10)
    ollama_timeout_seconds: int = Field(default=300, ge=10)  # one Qwen3 answer through Langflow
    thinking_timeout_seconds: int = Field(default=600, ge=30)  # read-only CLI call outside a project's limits
    # macOS: hold an idle-sleep assertion (caffeinate -i) while jobs are queued or running; released when idle this long
    sdlc_keep_awake: bool = True
    keep_awake_release_seconds: int = Field(default=120, ge=0)
    max_format_retries: int = Field(default=2, ge=0, le=5)  # thinking-role retries outside a project's limits
    process_output_max_chars: int = Field(default=200_000, ge=10_000)  # output kept from each subprocess
    job_log_read_bytes: int = Field(default=64_000, ge=4_000)  # one poll of a job's live log
    # Discovery: file types a source may have (the project's limits decide sizes and page counts).
    discovery_accepted_types: str = "pdf,docx,xlsx,pptx,png,jpg,jpeg,webp,md,txt,csv,eml"
    # The Business Analyst's engine for discovery runs, when it differs from SDLC_ENGINES (empty: the same).
    # codex-readonly reads PDFs as text made by pdftotext; it cannot read images.
    sdlc_discovery_engine: str = ""
    pdftotext_path: str = "pdftotext"
    pdftotext_timeout_seconds: int = Field(default=120, ge=5)
    langflow_max_response_bytes: int = Field(default=2_000_000, ge=100_000)
    # Previews: the application of a run started for people to try and for the acceptance tests (127.0.0.1 only).
    sdlc_preview_ports: str = "9100-9199"  # host ports a preview may take
    sdlc_logo_max_kb: int = 1024  # the customer's logo uploaded with an SDS request
    sdlc_defect_check_enabled: bool = True  # a person's defect notes are checked against the SDS (one model call)
    # earlier stories' acceptance tests that fail are judged outdated or broken by the Code Reviewer (one model call)
    sdlc_regression_triage_enabled: bool = True
    sdlc_advice_enabled: bool = True  # the Code Reviewer briefs the Tech Lead when a task stops (one model call)
    # comma-separated folders, besides the worktree and the tools on PATH, that an AI CLI session may read (macOS:
    # the command line tools that git and python3 need, and the OpenSSL configuration node reads)
    sdlc_executor_read_paths: str = "/Library/Developer/CommandLineTools,/System/Library/OpenSSL"
    sdlc_preview_max_running: int = Field(default=2, ge=1, le=20)  # more stops the oldest
    sdlc_preview_ttl_minutes: int = Field(default=60, ge=5)  # a preview stops this long after it started
    # Worker processes that run jobs side by side (one job per run at a time). Each validation and preview takes
    # Docker memory: raise it only when Docker has room (BACKLOG 3.18).
    sdlc_worker_concurrency: int = Field(default=1, ge=1, le=8)
    # the models a Codex login may use, as the Codex CLI caches them (the AI providers page offers them)
    sdlc_codex_models_cache: str = "~/.codex/models_cache.json"
    claim_candidates: int = Field(default=20, ge=1)  # queued jobs a worker looks through for a run that is free

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
