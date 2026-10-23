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


IMAGE = re.compile(r"[a-z0-9][\w.\-/:@]{0,199}")


class ValidationCommand(Strict):
    name: str
    run: list[str] = Field(min_length=1)
    timeout_seconds: int = Field(default=300, ge=1, le=3600)
    # Several languages: the command's own image (a toolchain of the platform such as python-3.13, or an image
    # reference such as eclipse-temurin:21-jdk) and the setup that installs its dependencies in that image
    image: str = ""
    setup: list[str] = []
    # lint: rules on how code is written (ruff, eslint) that hold whether or not the task's code exists yet; a lint
    # failure in new tests goes back to the QA Engineer and never counts as the red of TDD. check: everything else
    # (tests, type checks). auto: lint when the name says lint.
    kind: Literal["auto", "lint", "check"] = "auto"

    @property
    def is_lint(self) -> bool:
        return self.kind == "lint" or (self.kind == "auto" and "lint" in self.name)

    @field_validator("image")
    @classmethod
    def _image(cls, value: str) -> str:
        if value and not IMAGE.fullmatch(value):
            raise ValueError("image must be a toolchain name or an image reference such as eclipse-temurin:21-jdk")
        return value

    @field_validator("setup")
    @classmethod
    def _setup(cls, value: list[str]) -> list[str]:
        if any(not ARG.fullmatch(arg) for arg in value):
            raise ValueError("setup arguments must be non-empty single-line strings")
        return value

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


ENV_NAME = re.compile(r"[A-Z_][A-Z0-9_]{0,63}")


class Service(Strict):
    """A container the validation commands reach by name, such as a database. It runs in the validation's own
    network, which has no Internet access, and is removed afterwards."""
    name: str = Field(pattern=r"^[a-z][a-z0-9-]{0,30}$")  # its host name for the validation commands
    image: str = Field(min_length=1, max_length=200)
    env: dict[str, str] = {}
    ready: list[str] = []  # run inside the service until it succeeds, such as ["pg_isready", "-U", "app"]
    ready_timeout_seconds: int = Field(default=60, ge=5, le=600)

    @field_validator("env")
    @classmethod
    def _env(cls, value: dict[str, str]) -> dict[str, str]:
        if any(not ENV_NAME.fullmatch(k) for k in value):
            raise ValueError("environment variable names must be upper case letters, digits and '_'")
        return value


class Environment(Strict):
    """The project's own validation environment, for a stack the platform's toolchains do not cover."""
    dockerfile: str = ""  # repository path; the platform builds it into the validation image
    setup_inputs: list[str] = []  # files whose change runs setup again, such as dependency manifests
    # argv run with network in the validation image after a task changes a dependency manifest: it writes the
    # lockfiles (npm install --package-lock-only, uv lock), which a session without network cannot resolve
    lock: list[str] = []
    env: dict[str, str] = {}  # for the validation commands, such as the database URL of a service
    services: list[Service] = []

    @field_validator("dockerfile")
    @classmethod
    def _dockerfile(cls, value: str) -> str:
        return _check_pattern(value) if value else value

    @field_validator("setup_inputs")
    @classmethod
    def _inputs(cls, value: list[str]) -> list[str]:
        return [_check_pattern(pattern) for pattern in value]

    @field_validator("env")
    @classmethod
    def _env(cls, value: dict[str, str]) -> dict[str, str]:
        if any(not ENV_NAME.fullmatch(k) for k in value):
            raise ValueError("environment variable names must be upper case letters, digits and '_'")
        return value

    @model_validator(mode="after")
    def _unique(self) -> "Environment":
        names = [s.name for s in self.services]
        if len(names) != len(set(names)):
            raise ValueError("service names must be unique")
        return self


class Account(Strict):
    """A demo account of the preview: test data only."""
    role: str = Field(min_length=1, max_length=40)
    username: str = Field(min_length=1, max_length=120)
    password: str = Field(min_length=1, max_length=120)
    note: str = ""


class Preview(Strict):
    """The application started from a run's branch, for people to try and for the acceptance tests."""
    compose: str  # repository path of a docker compose file: the app, its database, test doubles, seed data
    service: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,62}$")  # the service opened on 127.0.0.1
    port: int = Field(ge=1, le=65535)  # that service's port inside its container
    path: str = "/"  # what Try it opens
    health_path: str = "/"  # answered with 200 once the application is up
    start_timeout_seconds: int = Field(default=600, ge=30, le=3600)  # from started to answering its health path
    build_timeout_seconds: int = Field(default=1800, ge=60, le=7200)  # building its images (dependencies download)
    accounts: list[Account] = []
    e2e: list[str] = []  # the acceptance tests: argv run in e2e_image next to the preview
    e2e_image: str = "mcr.microsoft.com/playwright:v1.49.1-noble"
    e2e_paths: list[str] = ["e2e/**"]  # where the QA Engineer writes them
    e2e_timeout_seconds: int = Field(default=900, ge=30, le=3600)

    @field_validator("compose")
    @classmethod
    def _compose(cls, value: str) -> str:
        return _check_pattern(value)

    @field_validator("e2e_paths")
    @classmethod
    def _paths(cls, value: list[str]) -> list[str]:
        return [_check_pattern(pattern) for pattern in value]

    @field_validator("path", "health_path")
    @classmethod
    def _url_path(cls, value: str) -> str:
        if not value.startswith("/"):
            raise ValueError("preview paths start with /")
        return value


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
    max_rounds: int = Field(default=10, ge=1, le=50)  # extraction rounds before the PO confirms another round
    max_questions_per_round: int = Field(default=30, ge=1, le=200)
    max_source_mb: int = Field(default=50, ge=1, le=500)
    max_pages_per_source: int = Field(default=300, ge=1, le=5000)
    # PDF pages in one Business Analyst call: Claude Code reads at most 20, and 20 dense pages made an answer longer
    # than the CLI's output limit (trial, 2026-10-16); a part whose answer is unusable is halved and read again.
    pages_per_read: int = Field(default=10, ge=1, le=20)
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
    options: Literal["gated", "skip"] = "gated"  # SDS runs: the Architect compares options, the SA picks one
    ui_concepts: Literal["gated", "skip"] = "gated"  # SDS runs: the UX Designer proposes concepts, the PO picks one
    # Story runs with a preview: E2E tests on the running application, then the QC's acceptance gate
    acceptance: Literal["gated", "auto", "skip"] = "gated"


class Ui(Strict):
    """The project's UI concept: how many concepts the SDS run proposes, and the gate that checks screens against it."""
    guide: str = "docs/ui/UI.md"
    tokens: str = "docs/ui/design-tokens.json"
    concepts: int = Field(default=3, ge=1, le=5)
    devices: list[Literal["desktop", "tablet", "mobile", "kiosk"]] = ["desktop", "mobile"]
    accessibility: Literal["wcag-aa", "wcag-aaa", "none"] = "wcag-aa"
    review: Literal["gated", "skip"] = "gated"  # the QC ticks "the UI follows the concept" at the acceptance gate


class Disputes(Strict):
    """Technical disputes between the AI roles (a test on the wrong layer, a fixture, a file the plan forgot) are
    settled by the Code Reviewer's brief without a person, when the brief is confident enough and touches neither
    the meaning of a criterion nor a security rule nor the design. ask: every dispute waits for the Tech Lead."""
    auto: bool = True
    min_confidence: Literal["high", "medium"] = "high"
    max_auto_per_task: int = Field(default=2, ge=0, le=5)  # then the Tech Lead decides


class Delivery(Strict):
    """How the stories of the approved backlog open: the worker opens the next ready story while fewer feature runs
    than max_open_runs are open. A story opens on its own only once; after a run that did not merge, a person starts it."""
    auto_start: bool = True
    max_open_runs: int = Field(default=2, ge=1, le=10)
    update_with_main: bool = True  # merge the default branch into open runs' branches at their gates (B3)


class Artifacts(Strict):
    commit_to_repo: bool = True


HOSTING_PLATFORMS = ("aws", "azure", "gcp", "on_premise", "hybrid")


class Architecture(Strict):
    """SDS runs: where the Architect may host each architecture option. A customer that accepts only its own data
    centre lists on_premise alone."""
    hosting_platforms: list[str] = Field(default=list(HOSTING_PLATFORMS), min_length=1)
    max_hosting_variants: int = Field(default=3, ge=1, le=5)  # hosting variants compared for each option


class ProjectConfig(Strict):
    schema_version: Literal[1]
    project: str
    default_branch: str = "main"
    validation_image: str = ""  # a toolchain of the platform; or environment.dockerfile
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
    architecture: Architecture = Architecture()
    environment: Environment = Environment()
    preview: Preview | None = None
    ui: Ui = Ui()
    delivery: Delivery = Delivery()
    disputes: Disputes = Disputes()

    @model_validator(mode="after")
    def _consistent(self) -> "ProjectConfig":
        if not self.validation_image and not self.environment.dockerfile and (
                self.setup or any(not command.image for command in self.validation)):
            raise ValueError("name a validation_image, or an environment.dockerfile the platform builds "
                             "(or an image on every validation command)")
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
    hosting_platforms: list[str] = list(HOSTING_PLATFORMS)
    max_hosting_variants: int = 3
    project: str = ""  # names the image built from environment.dockerfile
    env_dockerfile: str = ""  # with it, validation_image is built from this file of the worktree
    env: dict[str, str] = {}
    services: list[Service] = []
    setup_inputs: list[str] = []
    lock: list[str] = []
    preview: Preview | None = None
    ui: Ui = Ui()
    disputes: Disputes = Disputes()
    toolchains: dict[str, str] = {}  # platform toolchain name -> image, for a validation command's own image


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
        validation_image="" if config.environment.dockerfile else dict(defaults.validation_images).get(config.validation_image, ""),
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
        hosting_platforms=list(config.architecture.hosting_platforms),
        max_hosting_variants=config.architecture.max_hosting_variants,
        project=config.project,
        env_dockerfile=config.environment.dockerfile,
        env=dict(config.environment.env),
        services=list(config.environment.services),
        setup_inputs=list(config.environment.setup_inputs),
        lock=list(config.environment.lock),
        preview=config.preview,
        ui=config.ui,
        disputes=config.disputes,
        toolchains=dict(defaults.validation_images),
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
