"""The foundation run: after the SDS is merged, the project gets its onboarding files and its walking skeleton.

The Architect rewrites the onboarding files from the SDS (AGENTS.md, REVIEW.md, the technical part of
.sdlc/project.yaml, the validation Dockerfile, .gitignore, the CI workflow); a human approves them at the onboarding
gate and the controller commits them, since the coding roles may never touch them. Then the run builds the skeleton
every user story stands on, through the usual spec, plan, test, code, review and pull request.
"""

import difflib
import re

from pydantic import BaseModel, ConfigDict, Field
import yaml

from controller.app.projects.config import (Context, Environment, Paths, Preview, ValidationCommand,
                                            effective_policy, parse_project_config)
from controller.app.schemas import FeedbackReply, Question

CONFIG_PATH = ".sdlc/project.yaml"
DOCKERFILE_PATH = ".sdlc/validation.Dockerfile"
PREVIEW_COMPOSE_PATH = ".sdlc/preview.compose.yml"
CI_PATH = ".github/workflows/ci.yml"
# The parts of the SDS the foundation builds on: stack, structure, principles, API standards, security, conventions.
FOUNDATION_SECTIONS = ("4.3.1", "4.3.2", "4.3.3", "4.3.5", "4.7", "6.1", "6.6", "7.1", "7.2", "7.5", "12.1", "12.2",
                       "13.")
ONBOARDING_SECTIONS = ("4.1", "4.3", "4.5", "4.7", "6.1", "7.", "8.", "12.", "13.")


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FoundationRequest(Contract):
    title: str = Field(min_length=3, max_length=200)
    notes: str = ""  # what the skeleton must include or leave out


class OnboardingLimits(Contract):
    max_tasks: int = Field(default=8, ge=1, le=50)
    max_changed_files: int = Field(default=30, ge=1, le=500)


class TestDouble(Contract):
    """How the tests stand in for one system of SDS section 13: a service in the validation network, or why none."""
    system: str  # the integration, as SDS section 13 names it
    service: str = ""  # the name of its service in environment.services; empty when it needs none
    kind: str = ""  # what the double is, such as WireMock with mappings, MinIO, Mailpit, MediaMTX with a sample video
    fixtures: str = ""  # where its scenarios live in the repository
    notes: str = ""  # the scenarios it covers, or why it needs no service (an internal component, an in-process fake)


class Onboarding(Contract):
    """The Architect's onboarding files for the stack the SDS chose."""
    agents_md: str = Field(min_length=20)
    review_md: str = ""
    gitignore: str = ""
    ci_workflow: str = ""  # GitHub Actions running the validation commands; empty for none
    dockerfile: str = ""  # the validation image, when no platform toolchain fits
    validation_image: str = ""  # a platform toolchain (node-20, python-3.13 ...) when one fits the whole stack
    setup: list[str] = []
    validation: list[ValidationCommand] = Field(min_length=1)
    paths: Paths
    context: Context = Context()
    environment: Environment = Environment()
    limits: OnboardingLimits = OnboardingLimits()
    test_doubles: list[TestDouble] = []
    preview: Preview | None = None  # how the application starts for people to try and for the acceptance tests
    preview_compose: str = ""  # the docker compose file of the preview
    notes: str = ""  # what the reviewer should know: choices, what the skeleton must create first
    questions: list[Question] = []
    feedback_replies: list[FeedbackReply] = []


def sds_excerpt(sds: str, numbers: tuple[str, ...]) -> str:
    """The SDS sections whose numbers start with one of `numbers` (a heading "### 4.3 ..." for "4.3"), in order."""
    out, keep, level = [], False, 0
    for line in sds.splitlines():
        match = re.match(r"^(#{2,4}) +(\d+(?:\.\d+)*\.?)\s", line)
        if match:
            depth, number = len(match[1]), match[2].rstrip(".")
            if keep and depth <= level:
                keep = False
            if not keep and any(number == n.rstrip(".") or number.startswith(n if n.endswith(".") else f"{n}.")
                                for n in numbers):
                keep, level = True, depth
        if keep:
            out.append(re.sub(r"\s*<a id=\"[^\"]*\"></a>", "", line))
    return "\n".join(out).strip()


def foundation_intent(request: FoundationRequest, sds: str) -> str:
    """The intent of the foundation run: generic outcomes, the user's notes, and the parts of the SDS they rest on."""
    outcomes = [
        "The repository has the module structure, configuration and tooling of the SDS (section 4.3), and the "
        "test, lint and run commands of AGENTS.md work on it.",
        "Database migrations create the schema baseline the skeleton needs, and the application reads its "
        "configuration and secrets from the environment, as SDS section 7.5 says.",
        "A health endpoint (or the equivalent for the stack) proves each runnable part starts.",
        "Sign-in, sessions and role checks of SDS sections 7.1 and 7.2 work end to end, so every user story can "
        "rely on them.",
        "Errors follow the API standards and error catalog of SDS sections 6.1 and 6.6.",
        "Every external system of SDS section 13 is reached through an adapter in the integrations layer, at an "
        "address from configuration, and the tests run against its test double (the services of .sdlc/project.yaml "
        "with their scenarios in the repository), never against the real system.",
        "The preview of .sdlc/project.yaml starts: its docker compose file builds and runs the application with its "
        "database and test doubles, loads seed data with the demo accounts, and answers on its health path.",
    ]
    return "\n".join([
        f"# Intent: {request.title}", "",
        "## Problem", "",
        "The repository has no code for the design yet. Every user story needs the same structure, tooling and "
        "shared services first, so the foundation builds them once, before any story.", "",
        "## Desired outcome", "", *[f"- {o}" for o in outcomes], "",
        "## Constraints", "",
        "- No behaviour of any user story of SRS section 7 beyond what sign-in and role checks need.",
        "- Follow AGENTS.md and the SDS exactly: names, folders, layers and conventions.", "",
        "## Notes", "", request.notes.strip() or "_None._", "",
        "## Design reference (from SDS.md)", "", sds_excerpt(sds, FOUNDATION_SECTIONS) or "_SDS.md has none of these "
        "sections._", ""])


def sds_integrations(sds: str) -> list[str]:
    """The integrations SDS section 13 lists: the first cell of each row of its table."""
    section = sds_excerpt(sds, ("13.",))
    rows = [line.strip("|").split("|")[0].strip() for line in section.splitlines() if line.startswith("|")]
    return [r.strip("`*") for r in rows[2:] if r and not set(r) <= set("-: ")]


def check_test_doubles(onboarding: Onboarding, sds: str) -> list[str]:
    """Each integration of SDS section 13 has a test double, or a reason for none; each double names a real service."""
    def key(text: str) -> str:
        return re.sub(r"\W+", " ", text.lower()).strip()
    covered = {key(d.system) for d in onboarding.test_doubles}
    services = {s.name for s in onboarding.environment.services}
    problems = [f"{name}: no test double and no reason" for name in sds_integrations(sds) if key(name) not in covered]
    problems += [f"{d.system}: service {d.service!r} is not in environment.services" for d in onboarding.test_doubles
                 if d.service and d.service not in services]
    problems += [f"{d.system}: no service and no reason" for d in onboarding.test_doubles
                 if not d.service and not d.notes.strip()]
    return problems


def onboarding_files(onboarding: Onboarding, current_config: str) -> dict[str, str]:
    """The files the onboarding commits. project.yaml keeps everything but its technical part (project, executors,
    stages, discovery, architecture and other limits stay); the result must be a valid configuration."""
    config = yaml.safe_load(current_config) or {}
    config.pop("validation_image", None)
    config.pop("environment", None)
    environment = onboarding.environment.model_dump(exclude_defaults=True)
    if onboarding.dockerfile.strip():
        environment["dockerfile"] = DOCKERFILE_PATH
    if onboarding.validation_image and not onboarding.dockerfile.strip():
        config["validation_image"] = onboarding.validation_image
    config["setup"] = list(onboarding.setup)
    config["validation"] = [v.model_dump() for v in onboarding.validation]
    config["paths"] = onboarding.paths.model_dump()
    config["context"] = onboarding.context.model_dump()
    config["limits"] = {**(config.get("limits") or {}), **onboarding.limits.model_dump()}
    if environment:
        config["environment"] = environment
    if onboarding.preview is not None:
        config["preview"] = {**onboarding.preview.model_dump(exclude_defaults=True), "compose": PREVIEW_COMPOSE_PATH,
                             "service": onboarding.preview.service, "port": onboarding.preview.port}
    text = yaml.safe_dump(config, sort_keys=False, allow_unicode=True, width=120)
    effective_policy(parse_project_config(text))  # raises with the reason when the configuration is not valid
    files = {"AGENTS.md": onboarding.agents_md.rstrip() + "\n", CONFIG_PATH: text}
    for path, content in (("REVIEW.md", onboarding.review_md), (".gitignore", onboarding.gitignore),
                          (CI_PATH, onboarding.ci_workflow), (DOCKERFILE_PATH, onboarding.dockerfile),
                          (PREVIEW_COMPOSE_PATH, onboarding.preview_compose if onboarding.preview else "")):
        if content.strip():
            files[path] = content.rstrip() + "\n"
    return files


def render_onboarding(title: str, onboarding: Onboarding, files: dict[str, str], current: dict[str, str | None]) -> str:
    """The onboarding for the gate: the Architect's notes, then each file as a diff against the default branch."""
    out = [f"# Onboarding: {title}", "", onboarding.notes.strip() or "_No notes._", ""]
    if onboarding.questions:
        out += ["## Questions", "", *[f"- {q.question}" + (" (blocking)" if q.blocking else "")
                                      for q in onboarding.questions], ""]
    if onboarding.test_doubles:
        cell = lambda text: " ".join(str(text).split()).replace("|", "\\|") or "—"
        out += ["## External systems and their test doubles", "", "| System | Service | Double | Scenarios in | Notes |",
                "|---|---|---|---|---|",
                *[f"| {cell(d.system)} | {cell(d.service and f'`{d.service}`')} | {cell(d.kind)} | {cell(d.fixtures)} | "
                  f"{cell(d.notes)} |" for d in onboarding.test_doubles], ""]
    for path, content in files.items():
        before = current.get(path)
        out += [f"## `{path}` ({'changed' if before else 'new'})", ""]
        if before:
            diff = list(difflib.unified_diff(before.splitlines(), content.splitlines(), "default branch", "onboarding",
                                             lineterm="", n=2))
            out += ["````diff", *(diff or ["(no change)"]), "````", ""]  # four backticks: files hold fences of three
        else:
            language = "yaml" if path.endswith((".yaml", ".yml")) else "dockerfile" if "Dockerfile" in path else ""
            out += [f"````{language}", content.rstrip(), "````", ""]
    return "\n".join(out)
