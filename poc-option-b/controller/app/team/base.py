"""What each team role receives and returns. Implementations: mock (B2), Langflow/CLI (B3, B4)."""

from dataclasses import dataclass, field
from typing import Protocol

from controller.app.project_docs import Sds, Srs
from controller.app.projects.config import EffectivePolicy
from controller.app.schemas import Intent, PlanPayload, PlanTask, Review, Spec, TestPlan


@dataclass(frozen=True)
class RunContext:
    """Everything a role may read. Built by the coordinator from stored artifacts only."""
    title: str
    policy: EffectivePolicy
    agents_md: str
    review_md: str | None
    intent_md: str
    files: list[str]
    spec_md: str | None = None
    srs_md: str | None = None  # the SRS.md already on the base commit, if any
    sds_md: str | None = None  # the SDS.md already on the base commit, if any
    story: dict | None = None  # the SRS user story a feature run delivers, with its feature's requirements
    answers: list[tuple[str, str]] = field(default_factory=list)
    repository: dict = field(default_factory=dict)  # files, symbols, selected_files
    feedback: list[str] = field(default_factory=list)
    session_id: str = ""
    run_id: str = ""
    worktree: str = ""  # the run's git worktree, where the doing roles work
    base_commit: str = ""
    engines: dict = field(default_factory=dict)  # thinking role -> engine, experiments only


@dataclass(frozen=True)
class StepResult:
    ok: bool
    detail: str
    changed_files: list[str] = field(default_factory=list)
    dispute: bool = False
    evidence: dict = field(default_factory=dict)  # session usage, commit, validation reports


@dataclass(frozen=True)
class ReleaseResult:
    url: str | None
    number: int | None
    simulated: bool


class CapacityBusy(RuntimeError):
    """The model host has no capacity now; the job should wait and run later."""
    wait_seconds: int | None = None  # None: the worker's default wait


class SubscriptionLimit(CapacityBusy):
    """A CLI's subscription usage window is exhausted; committed work is kept and the job runs later."""
    wait_seconds = 900


@dataclass(frozen=True)
class CallRecord:
    """How a role produced its answer, stored on the execution."""
    engine: str
    prompt_version: str | None = None
    attempts: int = 1
    usage: dict = field(default_factory=dict)  # subscription CLIs: seconds, tokens, cost


class Team(Protocol):
    engine: str
    last_call: CallRecord | None

    def draft_intent(self, text: str, title: str) -> Intent: ...
    def specify(self, context: RunContext) -> Spec: ...
    def write_srs(self, context: RunContext) -> Srs: ...
    def write_sds(self, context: RunContext) -> Sds: ...
    def plan(self, context: RunContext) -> PlanPayload: ...
    def design_tests(self, context: RunContext, plan: PlanPayload) -> TestPlan: ...
    def write_tests(self, context: RunContext, task: PlanTask, tests: TestPlan, feedback: str = "",
                    amend: bool = False) -> StepResult: ...
    def red_check(self, context: RunContext, task: PlanTask, tests: TestPlan) -> StepResult: ...
    def implement(self, context: RunContext, task: PlanTask, attempt: int, tests: TestPlan | None = None,
                  feedback: str = "") -> StepResult: ...
    def validate(self, context: RunContext, task: PlanTask, attempt: int = 1) -> StepResult: ...
    def review(self, context: RunContext, plan: PlanPayload, tests: TestPlan, diff: str = "",
               validation: str = "") -> Review: ...
    def open_pull_request(self, context: RunContext, branch: str) -> ReleaseResult: ...
