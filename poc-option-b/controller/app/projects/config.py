"""`.sdlc/project.yaml` schema, platform defaults and effective policy."""

from typing import Literal
import re

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
import yaml

NAME = re.compile(r"[a-z][a-z0-9-]{0,39}")
ARG = re.compile(r"[^\x00\n\r]{1,512}")
EXECUTORS = ("codex", "claude")


def _check_pattern(pattern: str) -> str:
    if not pattern or pattern.startswith("/") or "\\" in pattern or ".." in pattern.split("/"):
        raise ValueError(f"path pattern must be relative inside the repository: {pattern!r}")
    return pattern


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ValidationCommand(Strict):
    name: str
    run: list[str] = Field(min_length=1)
    timeout_seconds: int = Field(default=300, ge=1, le=3600)

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        if not NAME.fullmatch(value):
            raise ValueError("validation name must be lowercase letters, digits and '-'")
        return value

    @field_validator("run")
    @classmethod
    def _argv(cls, value: list[str]) -> list[str]:
        if any(not ARG.fullmatch(arg) for arg in value):
            raise ValueError("validation arguments must be non-empty single-line strings")
        return value


class Paths(Strict):
    allowed: list[str] = Field(min_length=1)
    tests: list[str] = Field(min_length=1)
    protected: list[str] = []

    @field_validator("allowed", "tests", "protected")
    @classmethod
    def _patterns(cls, value: list[str]) -> list[str]:
        return [_check_pattern(pattern) for pattern in value]


class Testing(Strict):
    red_check: bool = True
    qa_executor: Literal["codex", "claude"] | None = None


class Context(Strict):
    include: list[str] = ["**"]
    exclude: list[str] = []

    @field_validator("include", "exclude")
    @classmethod
    def _patterns(cls, value: list[str]) -> list[str]:
        return [_check_pattern(pattern) for pattern in value]


class ContainerLimits(Strict):
    """Resources of the validation container (red check, validation, setup)."""
    cpus: float = Field(default=2, gt=0, le=16)
    memory_mb: int = Field(default=2048, ge=256, le=65536)
    pids: int = Field(default=512, ge=64, le=8192)
    tmp_mb: int = Field(default=256, ge=16, le=8192)  # size of the writable /tmp inside the container


class Limits(Strict):
    max_tasks: int = Field(default=8, ge=1, le=50)
    max_changed_files: int = Field(default=30, ge=1, le=500)
    session_timeout_minutes: int = Field(default=20, ge=1, le=180)  # one QA Engineer or Developer CLI session
    thinking_timeout_minutes: int = Field(default=10, ge=1, le=60)  # one call of a read-only CLI thinking role
    setup_timeout_minutes: int = Field(default=15, ge=1, le=120)  # the project's setup commands in the container
    max_remediations: int = Field(default=2, ge=0, le=5)  # Developer retries after a failed validation
    max_format_retries: int = Field(default=2, ge=0, le=5)  # thinking-role retries after an answer breaks the contract
    container: ContainerLimits = ContainerLimits()


class Role(Strict):
    id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,31}$")
    name: str = Field(min_length=1, max_length=80)


class DiscoveryLimits(Strict):
    max_rounds: int = Field(default=5, ge=1, le=50)  # re-extractions before the PO confirms another round
    max_questions_per_round: int = Field(default=30, ge=1, le=200)
    max_source_mb: int = Field(default=50, ge=1, le=500)
    max_pages_per_source: int = Field(default=300, ge=1, le=5000)
    pages_per_read: int = Field(default=20, ge=1, le=20)  # Claude Code reads at most 20 PDF pages per call
    chars_per_read: int = Field(default=30_000, ge=2_000, le=200_000)  # text view sent in one Business Analyst call


DEFAULT_ROLES = (("po", "Product Owner"), ("ba", "Business Analyst"), ("sa", "Solution Architect"),
                 ("ta", "Technical Architect"), ("qc", "QC"), ("dpo", "Data Protection Officer"),
                 ("sales", "Sales (customer contact)"))
DEFAULT_QUESTION_OWNERS = {"scope": "po", "actor": "po", "permission": "po", "rule": "po", "threshold": "po",
                           "data": "ba", "integration": "ta", "nfr": "ta", "exception": "ba", "acceptance": "qc",
                           "compliance": "dpo", "deployment": "sa", "conflict": "po", "missing_source": "sales",
                           "customer": "sales"}


class Discovery(Strict):
    """Raw requirements to a baseline. Roles and who answers which kind of question differ per project."""
    roles: list[Role] = [Role(id=i, name=n) for i, n in DEFAULT_ROLES]
    question_owners: dict[str, str] = DEFAULT_QUESTION_OWNERS
    approvers: dict[str, str] = {"baseline": "po", "srs": "po", "options": "sa", "sds": "ta"}
    commit_sources: bool = True  # raw files go to requirements/sources/ with the baseline (audited before upload)
    limits: DiscoveryLimits = DiscoveryLimits()

    @model_validator(mode="after")
    def _known_roles(self) -> "Discovery":
        ids = {r.id for r in self.roles}
        unknown = sorted({v for v in [*self.question_owners.values(), *self.approvers.values()] if v not in ids})
        if unknown:
            raise ValueError(f"discovery names roles {', '.join(unknown)} that discovery.roles does not declare")
        return self


class Stages(Strict):
    spec: Literal["manual", "gated", "auto"] = "gated"
    plan: Literal["manual", "gated"] = "gated"  # the plan gate cannot be removed
    coding: Literal["manual", "auto"] = "auto"
    review: Literal["manual", "auto"] = "auto"


class Artifacts(Strict):
    commit_to_repo: bool = True


class ProjectConfig(Strict):
    schema_version: Literal[1]
    project: str
    default_branch: str = "main"
    validation_image: str
    setup: list[str] = []
    validation: list[ValidationCommand] = Field(min_length=1)
    paths: Paths
    testing: Testing = Testing()
    context: Context = Context()
    executors: list[Literal["codex", "claude"]] = Field(min_length=1)
    default_executor: Literal["codex", "claude"]
    limits: Limits = Limits()
    stages: Stages = Stages()
    artifacts: Artifacts = Artifacts()
    discovery: Discovery = Discovery()

    @model_validator(mode="after")
    def _consistent(self) -> "ProjectConfig":
        if self.default_executor not in self.executors:
            raise ValueError("default_executor must be one of executors")
        if self.testing.qa_executor and self.testing.qa_executor not in self.executors:
            raise ValueError("testing.qa_executor must be one of executors")
        names = [command.name for command in self.validation]
        if len(names) != len(set(names)):
            raise ValueError("validation names must be unique")
        return self


def parse_project_config(text: str) -> ProjectConfig:
    data = yaml.safe_load(text)
    if not isinstance(data, dict):
        raise ValueError(".sdlc/project.yaml must be a mapping")
    return ProjectConfig.model_validate(data)


class PlatformDefaults(Strict):
    protected_paths: tuple[str, ...] = (
        ".env", ".env.*", "**/.env", "**/.env.*", "**/*.pem", "**/*.key", "**/secrets/**",
        ".github/workflows/**", "deploy/**", "infra/**", ".sdlc/project.yaml",
    )
    max_tasks: int = 8
    max_changed_files: int = 30
    enabled_executors: tuple[str, ...] = EXECUTORS
    # Toolchain names a project may use in validation_image, and the container image each one runs in.
    validation_images: tuple[tuple[str, str], ...] = (
        ("node-20", "node:20-alpine"), ("node-22", "node:22-alpine"),
        ("python-3.12", "python:3.12-slim"), ("python-3.13", "python:3.13-slim"),
    )


class EffectivePolicy(Strict):
    """The project configuration after platform defaults are applied. Projects can only tighten."""
    protected_paths: list[str]
    allowed_paths: list[str]
    test_paths: list[str]
    max_tasks: int
    max_changed_files: int
    executors: list[str]
    default_executor: str
    qa_executor: str
    validation: list[ValidationCommand]
    stages: Stages
    commit_artifacts: bool
    validation_image: str = ""  # container image; empty when the platform does not provide the toolchain
    setup: list[str] = []
    red_check: bool = True
    # Defaults match Limits, so policies stored by runs before these fields existed keep working.
    session_timeout_seconds: int = 1200
    thinking_timeout_seconds: int = 600
    setup_timeout_seconds: int = 900
    max_remediations: int = 2
    max_format_retries: int = 2
    container_cpus: float = 2
    container_memory_mb: int = 2048
    container_pids: int = 512
    container_tmp_mb: int = 256


def effective_policy(config: ProjectConfig, defaults: PlatformDefaults = PlatformDefaults()) -> EffectivePolicy:
    executors = [name for name in config.executors if name in defaults.enabled_executors]
    if config.default_executor not in executors:
        raise ValueError(f"executor {config.default_executor} is not enabled on this platform")
    return EffectivePolicy(
        protected_paths=sorted(set(defaults.protected_paths) | set(config.paths.protected)),
        allowed_paths=list(config.paths.allowed),
        test_paths=list(config.paths.tests),
        max_tasks=min(defaults.max_tasks, config.limits.max_tasks),
        max_changed_files=min(defaults.max_changed_files, config.limits.max_changed_files),
        executors=executors,
        default_executor=config.default_executor,
        qa_executor=config.testing.qa_executor or config.default_executor,
        validation=list(config.validation),
        stages=config.stages,
        commit_artifacts=config.artifacts.commit_to_repo,
        validation_image=dict(defaults.validation_images).get(config.validation_image, ""),
        setup=list(config.setup),
        red_check=config.testing.red_check,
        session_timeout_seconds=config.limits.session_timeout_minutes * 60,
        thinking_timeout_seconds=config.limits.thinking_timeout_minutes * 60,
        setup_timeout_seconds=config.limits.setup_timeout_minutes * 60,
        max_remediations=config.limits.max_remediations,
        max_format_retries=config.limits.max_format_retries,
        container_cpus=config.limits.container.cpus,
        container_memory_mb=config.limits.container.memory_mb,
        container_pids=config.limits.container.pids,
        container_tmp_mb=config.limits.container.tmp_mb,
    )


def glob_to_regex(pattern: str) -> re.Pattern[str]:
    """Repository glob: `**` spans directories, `*` and `?` stay within one segment."""
    out, i = [], 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return re.compile("".join(out) + r"\Z")


def matches_any(path: str, patterns: list[str] | tuple[str, ...]) -> bool:
    return any(glob_to_regex(pattern).match(path) for pattern in patterns)
