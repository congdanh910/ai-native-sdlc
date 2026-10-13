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


class Limits(Strict):
    max_tasks: int = Field(default=8, ge=1, le=50)
    max_changed_files: int = Field(default=30, ge=1, le=500)


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
