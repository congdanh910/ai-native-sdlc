"""What each team role receives and returns. Implementations: mock (B2), Langflow/CLI (B3, B4)."""

from dataclasses import dataclass, field
from typing import Protocol

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
    answers: list[tuple[str, str]] = field(default_factory=list)
    repository: dict = field(default_factory=dict)  # files, symbols, selected_files
    feedback: list[str] = field(default_factory=list)
    session_id: str = ""
    run_id: str = ""
    worktree: str = ""  # the run's git worktree, where the doing roles work


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


@dataclass(frozen=True)
class CallRecord:
    """How a role produced its answer, stored on the execution."""
    engine: str
    prompt_version: str | None = None
    attempts: int = 1


class Team(Protocol):
    engine: str
    last_call: CallRecord | None

    def draft_intent(self, text: str, title: str) -> Intent: ...
    def specify(self, context: RunContext) -> Spec: ...
    def plan(self, context: RunContext) -> PlanPayload: ...
    def design_tests(self, context: RunContext, plan: PlanPayload) -> TestPlan: ...
    def write_tests(self, context: RunContext, task: PlanTask, tests: TestPlan, feedback: str = "") -> StepResult: ...
    def red_check(self, context: RunContext, task: PlanTask, tests: TestPlan) -> StepResult: ...
    def implement(self, context: RunContext, task: PlanTask, attempt: int, tests: TestPlan | None = None,
                  feedback: str = "") -> StepResult: ...
    def validate(self, context: RunContext, task: PlanTask, attempt: int = 1) -> StepResult: ...
    def review(self, context: RunContext, plan: PlanPayload, tests: TestPlan) -> Review: ...
    def open_pull_request(self, context: RunContext, branch: str) -> ReleaseResult: ...
