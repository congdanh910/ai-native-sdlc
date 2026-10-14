"""Contracts between team roles: intent, spec, plan, test plan, review."""

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from controller.app.projects.repo import safe_repo_path

TASK_ID = re.compile(r"T\d{1,3}")
CRITERION = re.compile(r"^- (AC-\d{1,3}):\s*(\S.*)$", re.MULTILINE)


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Intent(Contract):
    title: str = Field(min_length=3, max_length=200)
    problem: str = Field(min_length=1)
    outcome: str = Field(min_length=1)
    users_and_systems: str = ""
    constraints: str = ""
    open_questions: str = ""


def render_intent(intent: Intent) -> str:
    sections = [("Problem", intent.problem), ("Desired outcome", intent.outcome),
                ("Affected users and systems", intent.users_and_systems), ("Constraints", intent.constraints),
                ("Open questions", intent.open_questions)]
    body = "\n\n".join(f"## {heading}\n\n{text.strip() or '_None._'}" for heading, text in sections)
    return f"# Intent: {intent.title}\n\n{body}\n"


class Question(Contract):
    question: str
    blocking: bool


class Criterion(Contract):
    id: str
    text: str


class Spec(Contract):
    goal: str
    user_stories: list[str]
    acceptance_criteria: list[Criterion] = Field(min_length=1)
    assumptions: list[str] = []
    concerns: list[str] = []
    questions: list[Question] = []


def render_spec(title: str, spec: Spec) -> str:
    def bullets(items: list[str]) -> str:
        return "\n".join(f"- {item}" for item in items) or "_None._"
    criteria = "\n".join(f"- {c.id}: {c.text}" for c in spec.acceptance_criteria)
    open_items = "\n".join(f"- {'[blocking] ' if q.blocking else ''}{q.question}" for q in spec.questions)
    return (f"# Spec: {title}\n\n## Goal\n\n{spec.goal}\n\n## User stories\n\n{bullets(spec.user_stories)}\n\n"
            f"## Acceptance criteria\n\n{criteria}\n\n## Assumptions\n\n{bullets(spec.assumptions)}\n\n"
            f"## Concerns\n\n{bullets(spec.concerns)}\n\n## Open questions\n\n{open_items or '_None._'}\n")


def parse_criteria(spec_markdown: str) -> list[Criterion]:
    """Acceptance criteria are lines `- AC-n: text`; a human-edited spec must keep that shape."""
    criteria = [Criterion(id=match[0], text=match[1].strip()) for match in CRITERION.findall(spec_markdown)]
    if not criteria:
        raise ValueError("spec must list acceptance criteria as lines like `- AC-1: ...`")
    if len({c.id for c in criteria}) != len(criteria):
        raise ValueError("acceptance criterion ids must be unique")
    return criteria


class Reference(Contract):
    file: str
    symbol: str | None = None


class PlanTask(Contract):
    id: str
    title: str = Field(min_length=3)
    depends_on: list[str] = []
    modify: list[str] = []
    create: list[str] = []
    provides: list[str] = []
    references: list[Reference] = []
    acceptance_criteria: list[str] = Field(min_length=1)
    validation: list[str] = Field(min_length=1)

    @field_validator("id")
    @classmethod
    def _id(cls, value: str) -> str:
        if not TASK_ID.fullmatch(value):
            raise ValueError("task id must look like T1")
        return value

    @field_validator("modify", "create")
    @classmethod
    def _paths(cls, value: list[str]) -> list[str]:
        return [safe_repo_path(path) for path in value]


class PlanPayload(Contract):
    summary: str
    tasks: list[PlanTask] = Field(min_length=1)

    @model_validator(mode="after")
    def _dag(self) -> "PlanPayload":
        ids = [task.id for task in self.tasks]
        if len(ids) != len(set(ids)):
            raise ValueError("task ids must be unique")
        known = set(ids)
        for task in self.tasks:
            unknown = set(task.depends_on) - known
            if unknown:
                raise ValueError(f"{task.id} depends on unknown tasks {sorted(unknown)}")
        visiting, done = set(), set()

        def visit(task_id: str) -> None:
            if task_id in done:
                return
            if task_id in visiting:
                raise ValueError(f"dependency cycle through {task_id}")
            visiting.add(task_id)
            for dependency in next(t for t in self.tasks if t.id == task_id).depends_on:
                visit(dependency)
            visiting.discard(task_id)
            done.add(task_id)
        for task_id in ids:
            visit(task_id)
        return self


class TestCase(Contract):
    __test__ = False  # not a pytest class
    id: str
    task_id: str
    criterion: str
    description: str
    expected: str
    target_file: str
    expected_pass: bool = False

    @field_validator("target_file")
    @classmethod
    def _path(cls, value: str) -> str:
        return safe_repo_path(value)


class TestPlan(Contract):
    __test__ = False
    cases: list[TestCase] = Field(min_length=1)


def render_plan(title: str, version: int, plan: PlanPayload, tests: TestPlan, grounding: dict | None) -> str:
    lines = [f"# Plan v{version}: {title}", "", plan.summary, "", "## Tasks", ""]
    for task in plan.tasks:
        lines.append(f"### {task.id} {task.title}")
        if task.depends_on:
            lines.append(f"- Depends on: {', '.join(task.depends_on)}")
        if task.modify:
            lines.append(f"- Modify: {', '.join(f'`{p}`' for p in task.modify)}")
        if task.create:
            lines.append(f"- Create: {', '.join(f'`{p}`' for p in task.create)}")
        if task.provides:
            lines.append(f"- Provides: {', '.join(f'`{name}`' for name in task.provides)}")
        lines.append(f"- Acceptance criteria: {', '.join(task.acceptance_criteria)}")
        lines.append(f"- Validation: {', '.join(task.validation)}")
        lines.append("")
    lines += ["## Test plan", "", "| Case | Task | Criterion | Test | Expected | File |", "|---|---|---|---|---|---|"]
    for case in tests.cases:
        mark = " (expected pass)" if case.expected_pass else ""
        lines.append(f"| {case.id} | {case.task_id} | {case.criterion} | {case.description}{mark} | "
                     f"{case.expected} | `{case.target_file}` |")
    status = (grounding or {}).get("status", "not run")
    lines += ["", f"## Grounding check: {status}", ""]
    for finding in (grounding or {}).get("findings", []):
        lines.append(f"- {finding}")
    for warning in (grounding or {}).get("warnings", []):
        lines.append(f"- Warning: {warning}")
    return "\n".join(lines).rstrip() + "\n"


class Finding(Contract):
    severity: Literal["important", "nit"]
    file: str | None = None
    text: str


class Review(Contract):
    verdict: Literal["pass", "concerns"]
    findings: list[Finding] = []
    coverage: dict[str, list[str]] = {}
    markdown: str = ""


def render_review(title: str, review: Review, engine: str) -> str:
    rows = "\n".join(f"| {criterion} | {', '.join(cases) or 'none'} |" for criterion, cases in review.coverage.items())
    findings = "\n".join(f"- {f.severity.capitalize()}: {f.text}" + (f" (`{f.file}`)" if f.file else "")
                         for f in review.findings) or "_None._"
    return (f"# Review: {title}\n\nVerdict: **{review.verdict}** (Code Reviewer, {engine})\n\n"
            f"## Acceptance-criteria coverage\n\n| Criterion | Test cases |\n|---|---|\n{rows}\n\n"
            f"## Findings\n\n{findings}\n")
