"""Contracts between team roles: intent, spec, plan, test plan, review."""

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from controller.app.projects.repo import safe_repo_path

TASK_ID = re.compile(r"T\d{1,3}")
CRITERION = re.compile(r"^- (AC-\d{1,3}):\s*(\S.*)$", re.MULTILINE)  # older runs: "- AC-1: text"
# The company format: "- **AC-01 — Short name:**" then indented Given / When / Then lines.
GWT_CRITERION = re.compile(r"^- \*\*((?:AC|EC)-\d{2,3}) — (.+?):\*\*[ \t]*\n((?:(?:[ \t]+.*)?\n?)*)", re.MULTILINE)


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Intent(Contract):
    title: str = Field(min_length=3, max_length=200)
    problem: str = Field(min_length=1)
    outcome: str = Field(min_length=1)
    users_and_systems: str = ""
    constraints: str = ""
    open_questions: str = ""
    story: str = ""  # the SRS user story this run delivers, such as AT-US-01; empty for projects without an SRS


def render_intent(intent: Intent) -> str:
    story = f"Delivers {intent.story} from SRS.md.\n\n" if intent.story else ""
    sections = [("Problem", intent.problem), ("Desired outcome", intent.outcome),
                ("Affected users and systems", intent.users_and_systems), ("Constraints", intent.constraints),
                ("Open questions", intent.open_questions)]
    body = "\n\n".join(f"## {heading}\n\n{text.strip() or '_None._'}" for heading, text in sections)
    return f"# Intent: {intent.title}\n\n{story}{body}\n"


class SelfCheck(Contract):
    """A role's answer to one item of its gate's checklist."""
    id: str
    status: Literal["pass", "deferred", "fail", "n/a"]
    note: str = ""


class Question(Contract):
    question: str
    blocking: bool


class FeedbackReply(Contract):
    """How a rewrite handled one point of the reviewer's feedback, so the reviewer can check it at the next gate."""
    feedback: str  # the point, in short
    change: str  # what changed for it, or why nothing did
    where: list[str] = []  # sections or elements it touched


class Criterion(Contract):
    id: str
    text: str  # the short name, 2-4 words in the company format
    given: str = ""  # several conditions: one per line, each extra one joined with "and"
    when: str = ""
    then: str = ""

    @property
    def full(self) -> str:
        """One line for roles that read criteria as text: the name and its Given / When / Then."""
        parts = [f"{word} {' and '.join(line.strip() for line in value.splitlines() if line.strip())}"
                 for word, value in (("Given", self.given), ("when", self.when), ("then", self.then)) if value.strip()]
        joined = ", ".join(parts)
        return f"{self.text}: {joined}" if joined else self.text


class Spec(Contract):
    goal: str
    user_stories: list[str]
    acceptance_criteria: list[Criterion] = Field(min_length=1)
    edge_cases: list[Criterion] = []
    self_check: list[SelfCheck] = []
    assumptions: list[str] = []
    concerns: list[str] = []
    questions: list[Question] = []
    feedback_replies: list[FeedbackReply] = []


NOTES_HEADING = "### Business Analyst notes"


def gwt_lines(criterion: Criterion) -> list[str]:
    """Given / When / Then, each on its own line; extra conditions as indented **and** lines (company template)."""
    lines = [f"- **{criterion.id} — {criterion.text}:**  "]
    clauses = [("Given", criterion.given), ("When", criterion.when), ("Then", criterion.then)]
    clauses = [(word, [part.strip().rstrip(",.") for part in value.splitlines() if part.strip()]) for word, value in clauses]
    clauses = [(word, parts) for word, parts in clauses if parts]
    rendered = []
    for word, parts in clauses:
        rendered.append(f"  **{word}** {parts[0]},")
        rendered += [f"    **and** {part}," for part in parts[1:]]
    if rendered:
        rendered[-1] = rendered[-1][:-1] + "."
    return lines + [line + "  " for line in rendered[:-1]] + rendered[-1:]


def render_spec(title: str, spec: Spec, *, story: dict | None = None) -> str:
    """One user story section in the company spec template. `story` (from the SRS) gives the id, statement and
    feature group; without it the section is headed by the run title (projects without an SRS)."""
    def bullets(items: list[str]) -> str:
        return "\n".join(f"- {item}" for item in items) or "_None._"
    if story:
        lines = [f"## {story['id']}: {story['title']}", "",
                 "> **Jira Story:** TBD  ", f"> **Feature group:** {story['feature_group']}", "", story["statement"]]
    else:
        lines = [f"## {title}", "", *(spec.user_stories[:1] or [f"**As a** user, **I want to** {title.lower()}."])]
    lines += ["", "### Acceptance Criteria", ""]
    for criterion in spec.acceptance_criteria:
        lines += gwt_lines(criterion)
    if spec.edge_cases:
        lines += ["", "### Edge Cases", ""]
        for criterion in spec.edge_cases:
            lines += gwt_lines(criterion)
    open_items = "\n".join(f"- {'[blocking] ' if q.blocking else ''}{q.question}" for q in spec.questions)
    lines += ["", NOTES_HEADING, "", f"**Goal:** {spec.goal}", "", "**Assumptions**", "", bullets(spec.assumptions), "",
              "**Concerns**", "", bullets(spec.concerns), "", "**Open questions**", "", open_items or "_None._"]
    return "\n".join(lines).rstrip() + "\n"


def _parse_gwt(name: str, body: str) -> tuple[str, str, str]:
    clauses = {"Given": [], "When": [], "Then": []}
    current = None
    for raw in body.splitlines():
        line = raw.strip().rstrip()
        match = re.match(r"\*\*(Given|When|Then|and)\*\*\s*(.*)", line)
        if not match:
            continue
        word, text = match[1], match[2].strip().rstrip(",.").strip()
        if word != "and":
            current = word
        if current and text:
            clauses[current].append(text)
    return tuple("\n".join(clauses[w]) for w in ("Given", "When", "Then"))


def parse_criteria(spec_markdown: str) -> list[Criterion]:
    """Acceptance criteria and edge cases of a spec, in either format: the company template
    (`- **AC-01 — Name:**` with Given / When / Then lines) or the older `- AC-1: text` lines."""
    criteria = []
    for match in GWT_CRITERION.finditer(spec_markdown.split(NOTES_HEADING)[0]):
        given, when, then = _parse_gwt(match[2], match[3])
        criteria.append(Criterion(id=match[1], text=match[2].strip(), given=given, when=when, then=then))
    if not criteria:
        criteria = [Criterion(id=m[0], text=m[1].strip()) for m in CRITERION.findall(spec_markdown)]
    if not criteria:
        raise ValueError("spec must list acceptance criteria like `- **AC-01 — Short name:**` followed by "
                         "Given / When / Then lines")
    if len({c.id for c in criteria}) != len(criteria):
        raise ValueError("acceptance criterion ids must be unique")
    if not any(c.id.startswith("AC-") for c in criteria):
        raise ValueError("spec needs at least one acceptance criterion (AC-01)")
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


class DesignFinding(Contract):
    """A spec gap (G), ambiguity (A) or design review finding (D) the Tech Lead found and how it is resolved."""
    type: str = "Gap"
    criterion: str = ""
    finding: str
    resolution: str = ""


class Row(Contract):
    name: str
    detail: str
    extra: str = ""


class Sequence(Contract):
    title: str
    mermaid: str  # a sequenceDiagram body; happy path first, one alt per error, steps tagged [AC-01]


class PlanDesign(Contract):
    """The company plan template's Architecture sections. All optional: a small change may need few of them."""
    preflight: list[DesignFinding] = []  # spec gaps and ambiguities found before designing
    findings: list[DesignFinding] = []  # design review findings against the project's rules
    domain_objects: list[Row] = []  # domain object | database entity | key fields
    business_rules: list[Row] = []  # rule | source AC | enforcement
    sequences: list[Sequence] = []
    error_flows: list[Row] = []  # scenario | HTTP status or outcome | error code
    rule_notes: list[Row] = []  # project rule id | Satisfied / Required / Note | note
    element_ids: list[Row] = []  # UI element | id | file


class PlanPayload(Contract):
    summary: str
    tasks: list[PlanTask] = Field(min_length=1)
    design: PlanDesign = PlanDesign()
    self_check: list[SelfCheck] = []

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
    name: str = ""  # short test name for test_cases.md
    type: Literal["unit", "integration", "e2e"] = "unit"
    severity: Literal["Critical", "Major", "Minor"] = "Major"

    @field_validator("target_file")
    @classmethod
    def _path(cls, value: str) -> str:
        return safe_repo_path(value)


class TestPlan(Contract):
    __test__ = False
    cases: list[TestCase] = Field(min_length=1)
    self_check: list[SelfCheck] = []


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
