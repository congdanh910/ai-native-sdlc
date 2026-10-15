"""A feature run's artifacts in the company layout: specs/NNN-slug/ with one file per feature.

Each run delivers one user story from SRS section 7. Its sections are appended to the
feature's files (spec.md, checklists/requirements.md, plan.md, tasks.md, test_cases.md,
sdlc.yaml) or replace the story's earlier section; other stories' sections are never touched.
The SRS gets the spec link once on its feature, the SDS the plan link once on its feature.
"""

from datetime import date
import re

from pydantic import BaseModel
import yaml

from controller.app.project_docs import SDS_PATH, SRS_PATH, TAGS, _section_text, _slug, srs_features
from controller.app.schemas import NOTES_HEADING, Criterion, PlanPayload, Spec, TestPlan

SPECS_DIR = "specs"
US_HEADING = re.compile(r"^## ([A-Z]{2,4}-US-\d{2}(?:\.\d+)?):", re.MULTILINE)
PHASE_HEADING = re.compile(r"^## Phase \d+: ([A-Z]{2,4}-US-\d{2}(?:\.\d+)?):", re.MULTILINE)
FOLDER = re.compile(rf"^{SPECS_DIR}/(\d{{3}})-([a-z0-9-]+)/")


class FeatureRef(BaseModel):
    """Where a story lives: in the SRS and in specs/."""
    story_id: str
    story_title: str
    actors: str
    statement: str
    srs_section: str  # 7.1.1
    feature_section: str  # 7.1
    feature_name: str
    prefix: str
    feature_id: str  # 001
    slug: str
    requirements: str = ""  # the feature's functional requirements from SRS section 3, for the Business Analyst
    summary: str = ""

    @property
    def folder(self) -> str:
        return f"{SPECS_DIR}/{self.feature_id}-{self.slug}"

    def story(self) -> dict:
        """What render_spec needs to head the section."""
        return {"id": self.story_id, "title": f"{self.story_title} ({self.actors})", "statement": self.statement,
                "feature_group": f"{self.feature_section} {self.feature_name}"}


def resolve_feature(srs: str, files: list[str], read, story_id: str) -> FeatureRef:
    """Find the story in the SRS and its feature folder: the one whose sdlc.yaml has the story prefix,
    or the next free number. `read(path)` returns a file at the base commit or None."""
    for feature in srs_features(srs):
        story = next((s for s in feature.stories if s.id == story_id), None)
        if story:
            break
    else:
        raise ValueError(f"{story_id} is not a user story in SRS.md section 7")
    folders = sorted({m.groups() for m in (FOLDER.match(f) for f in files) if m})
    feature_id, slug = None, None
    for number, name in folders:
        context = yaml.safe_load(read(f"{SPECS_DIR}/{number}-{name}/sdlc.yaml") or "") or {}
        if str(context.get("story_prefix", "")).upper() == feature.prefix:
            feature_id, slug = number, name
            break
    if feature_id is None:
        feature_id = f"{max([int(n) for n, _ in folders] or [0]) + 1:03d}"
        slug = _slug(feature.name)
    requirements, summary = "", ""
    for match in re.finditer(r"^###\s+3\.\d+\s+(.+?)\s*$", TAGS.sub("", _section_text(srs, "3.")), re.MULTILINE):
        if _slug(match[1]).startswith(_slug(feature.name)) or _slug(feature.name).startswith(_slug(match[1])):
            body = TAGS.sub("", _section_text(srs, "3.")).split(match[0], 1)[1]
            body = re.split(r"^###\s", body, maxsplit=1, flags=re.MULTILINE)[0].strip()
            requirements = body
            summary = next((line for line in body.splitlines() if line.strip() and not line.startswith("|")), "")
            break
    return FeatureRef(story_id=story.id, story_title=story.title, actors=story.actors, statement=story.statement,
                      srs_section=story.section, feature_section=feature.section, feature_name=feature.name,
                      prefix=feature.prefix, feature_id=feature_id, slug=slug, requirements=requirements,
                      summary=summary)


# ---------------------------------------------------------------- sections inside a feature file

def upsert_section(text: str | None, header: str, story_id: str, section: str, heading: re.Pattern = US_HEADING) -> str:
    """Replace the story's section, or append it; every section ends with a `---` line."""
    section = section.rstrip() + "\n\n---\n"
    if not text or not text.strip():
        return header.rstrip() + "\n\n" + section
    starts = [m for m in heading.finditer(text)]
    mine = next((m for m in starts if m[1] == story_id), None)
    if mine is None:
        return text.rstrip() + ("\n\n" if text.rstrip().endswith("---") else "\n\n---\n\n") + section
    following = [m.start() for m in starts if m.start() > mine.start()]
    end = following[0] if following else len(text)
    return text[:mine.start()] + section + ("\n" + text[end:] if following else "")


def _today(today: date | None) -> str:
    return (today or date.today()).isoformat()


def spec_header(ref: FeatureRef, today: date | None = None) -> str:
    anchor = _slug(f"{ref.feature_section} {ref.feature_name} ({ref.prefix})")
    return (f"# Feature: {ref.feature_name}\n\n"
            f"> **Prefix:** `{ref.prefix}`  \n> **Jira Epic:** TBD  \n"
            f"> **SRS:** [SRS.md §{ref.feature_section}](../../{SRS_PATH}#{anchor})  \n"
            f"> **Created:** {_today(today)} · **Updated:** {_today(today)}\n\n---\n\n"
            f"## Overview\n\n{ref.summary or ref.feature_name + '.'}\n\n---")


def spec_section(gate_document: str) -> str:
    """The accepted spec without the Business Analyst's notes, which go to the requirements checklist."""
    body = gate_document.split(NOTES_HEADING)[0].rstrip()
    return (body + "\n\n- [x] Self-checked against [aif-review-checklist.md § `spec.md` Content]"
                   "(../../aif-review-checklist.md#specmd-content)\n"
                   "- [x] Self-checked against [aif-review-checklist.md § Traceability]"
                   "(../../aif-review-checklist.md#traceability)")


REQUIREMENT_ITEMS = {
    "Content Quality": ["No implementation details (languages, frameworks, APIs)",
                        "Focused on user value and business needs", "Written for non-technical stakeholders",
                        "All mandatory sections completed"],
    "Requirement Completeness": ["No [NEEDS CLARIFICATION] markers remain", "Requirements are testable and unambiguous",
                                 "Success criteria are measurable", "Success criteria are technology-agnostic (no implementation details)",
                                 "All acceptance scenarios are defined", "Edge cases are identified",
                                 "Scope is clearly bounded", "Dependencies and assumptions identified"],
    "Feature Readiness": ["All functional requirements have clear acceptance criteria",
                          "User scenarios cover primary flows",
                          "Feature meets measurable outcomes defined in Success Criteria",
                          "No implementation details leak into specification"],
}


def requirements_header(ref: FeatureRef, today: date | None = None) -> str:
    return (f"# Specification Quality Checklist: {ref.feature_name} ({ref.prefix})\n\n"
            "**Purpose**: Validate specification completeness and quality before proceeding to planning  \n"
            f"**Created**: {_today(today)}  \n**Feature**: [spec.md](../spec.md)  \n"
            "**Deferred backlog**: TBD  \n"
            "**Legend**: `[x]` satisfied · `[~]` deferred — gap acknowledged, fix postponed to the backlog · "
            "`[ ]` must be resolved before implementation")


def requirements_section(ref: FeatureRef, spec: Spec, checks: dict[str, tuple[str, str]] | None = None) -> str:
    """The spec-kit requirement items for this story. `checks` maps an item to (status, note) from the Business
    Analyst's self-check: pass -> [x], deferred -> [~] with its reason, anything else -> [ ]."""
    checks = checks or {}
    lines = [f"## {ref.story_id}: {ref.story_title}", ""]
    for group, items in REQUIREMENT_ITEMS.items():
        lines += [f"### {group}"]
        for item in items:
            status, note = checks.get(item, ("open", ""))
            box = {"pass": "x", "deferred": "~"}.get(status, " ")
            suffix = f" - {note}" if box == "~" and note else ""
            lines.append(f"- [{box}] {item}{suffix}")
        lines.append("")
    notes = [f"Goal: {spec.goal}", *[f"Assumption: {a}" for a in spec.assumptions],
             *[f"Concern: {c}" for c in spec.concerns]]
    lines += ["### Notes", *[f"- {n}" for n in notes]]
    return "\n".join(lines)


def sdlc_yaml(existing: str | None, ref: FeatureRef) -> str:
    context = yaml.safe_load(existing or "") or {}
    context.setdefault("feature_id", ref.feature_id)
    context.setdefault("slug", ref.slug)
    context.setdefault("feature_folder", ref.folder)
    context.setdefault("jira_epic", "TBD")
    context.setdefault("story_prefix", ref.prefix)
    stories = context.get("stories") or {}
    stories[ref.story_id] = {**(stories.get(ref.story_id) or {"jira_story": "TBD"}), "title": ref.story_title}
    context["stories"] = dict(sorted(stories.items()))
    return ("# SDLC context of this feature, kept by the AI-Native SDLC controller\n"
            + yaml.safe_dump(context, sort_keys=False, allow_unicode=True))


def link_once(document: str | None, heading: re.Pattern, label: str, target: str) -> str | None:
    """Add `> Label: [target](target)` right under the feature heading, once. None if the heading is missing."""
    if not document:
        return None
    match = heading.search(document)
    if not match:
        return None
    block_end = re.search(r"^#{2,3}\s", document[match.end():], re.MULTILINE)
    block = document[match.end(): match.end() + block_end.start()] if block_end else document[match.end():]
    if f"> {label}:" in block:
        return document
    line_end = document.index("\n", match.end()) if "\n" in document[match.end():] else len(document)
    return document[:line_end] + f"\n\n> {label}: [{target}]({target})" + document[line_end:]


def srs_feature_heading(ref: FeatureRef) -> re.Pattern:
    return re.compile(rf"^###\s+(?:<[^>]+>)?{re.escape(ref.feature_section)}\s.*$", re.MULTILINE)


def sds_feature_heading(ref: FeatureRef, sds: str) -> re.Pattern | None:
    """SDS section 5 mirrors SRS section 7, but numbering may differ: find the feature by its prefix."""
    match = re.search(rf"^###\s+(?:<[^>]+>)?(5\.\d+)\s.*\({re.escape(ref.prefix)}\).*$", sds, re.MULTILINE)
    return re.compile(re.escape(match[0]), re.MULTILINE) if match else None


# ---------------------------------------------------------------- plan, tasks, test cases

def _cell(text: str) -> str:
    return " ".join(str(text).split()).replace("|", "\\|") or "—"


def _table(header: list[str], rows: list[list[str]], empty: str) -> list[str]:
    if not rows:
        return [empty]
    return ["| " + " | ".join(header) + " |", "|" + "---|" * len(header),
            *["| " + " | ".join(_cell(c) for c in row) + " |" for row in rows]]


def plan_header(ref: FeatureRef) -> str:
    return (f"# Plan: {ref.feature_name} ({ref.prefix})\n\n"
            f"> **Feature:** {ref.feature_section}  \n> **Jira Epic:** TBD  \n> **Spec:** [spec.md](spec.md)  \n"
            f"> **SDS:** [SDS.md §5](../../{SDS_PATH})\n\n---")


def package_layout(plan: PlanPayload) -> list[str]:
    created = {p for t in plan.tasks for p in t.create}
    paths = sorted({p for t in plan.tasks for p in [*t.modify, *t.create]})
    width = max((len(p) for p in paths), default=0)
    return ["```text", *[f"{p.ljust(width)}  # {'new' if p in created else 'modify'}: "
                         f"{', '.join(t.id for t in plan.tasks if p in t.modify or p in t.create)}" for p in paths],
            "```"]


def plan_section(ref: FeatureRef, plan: PlanPayload, criteria: list[Criterion]) -> str:
    design = plan.design
    lines = [f"## {ref.story_id}: {ref.story_title}", "", "> **Jira Story:** TBD", "", plan.summary.strip(), "",
             "### Pre-flight: Spec Review Findings", ""]
    lines += _table(["ID", "Type", "AC", "Finding", "Resolution"],
                    [[f"{'A' if f.type.lower().startswith('amb') else 'G'}{i}", f.type, f.criterion, f.finding,
                      f.resolution] for i, f in enumerate(design.preflight, start=1)], "No spec gaps found.")
    lines += ["", "### Architecture", "", "**Design Review Findings**", ""]
    lines += _table(["ID", "Rule", "Finding", "Resolution"],
                    [[f"D{i}", f.criterion or f.type, f.finding, f.resolution] for i, f in enumerate(design.findings, start=1)],
                    "No design findings.")
    lines += ["", "**Package layout**", "", *package_layout(plan), "", "**Domain objects**", ""]
    lines += _table(["Domain Object", "Database Entity", "Key Fields"],
                    [[r.name, r.detail, r.extra] for r in design.domain_objects], "No new domain objects.")
    lines += ["", "**Business rules enforced in service layer**", ""]
    lines += _table(["Rule", "Rule text", "Source", "Enforcement"],
                    [[f"{ref.prefix}-BR-{i:02d}", r.name, r.extra, r.detail] for i, r in enumerate(design.business_rules, start=1)],
                    "No new business rules.")
    for sequence in design.sequences:
        lines += ["", f"**Sequence diagram — {sequence.title}**", "", "```mermaid", sequence.mermaid.strip(), "```"]
    lines += ["", "**Error flows**", ""]
    lines += _table(["Scenario", "HTTP", "Error Code"], [[r.name, r.detail, r.extra] for r in design.error_flows],
                    "No error flows.")
    lines += ["", "**Constitution notes**", ""]
    lines += _table(["Rule", "Status", "Note"], [[r.name, r.detail, r.extra] for r in design.rule_notes],
                    "No project rules apply.")
    lines += ["", "**Element IDs**", ""]
    lines += _table(["Element", "ID", "Status", "File"], [[r.name, f"`{r.detail}`", "Missing", r.extra]
                                                          for r in design.element_ids], "No new UI elements.")
    known = {c.id for c in criteria}
    lines += ["", "**Open tasks**", ""]
    lines += _table(["Task", "Description", "Files", "AC"],
                    [[t.id, t.title, ", ".join(f"`{p}`" for p in [*t.modify, *t.create]),
                      ", ".join(a for a in t.acceptance_criteria if a in known) or "—"] for t in plan.tasks], "")
    return "\n".join(lines)


def tasks_header(ref: FeatureRef) -> str:
    return (f"# Tasks: {ref.feature_name}\n\n**Input**: [spec.md](spec.md) · [plan.md](plan.md) · "
            "[test_cases.md](test_cases.md)\n\n## Format: `[ID] [P?] [Story] Description`\n\n"
            "- **[P]**: can run in parallel (different files, no dependencies)\n"
            f"- **[Story]**: the user story, such as [{ref.prefix}-US-01]\n\n---")


def phase_number(existing: str | None, story_id: str) -> int:
    """The story keeps its phase number; a new story gets the next one."""
    for match in re.finditer(r"^## Phase (\d+): ([A-Z]{2,4}-US-\d{2}(?:\.\d+)?):", existing or "", re.MULTILINE):
        if match[2] == story_id:
            return int(match[1])
    return max([int(n) for n in re.findall(r"^## Phase (\d+):", existing or "", re.MULTILINE)] or [0]) + 1


def tasks_section(ref: FeatureRef, plan: PlanPayload, tests: TestPlan, existing: str | None, phase: int,
                  passed: set[str] = frozenset(), numbers: dict[str, str] | None = None) -> str:
    """Tests before the code they prove; T### numbers continue the file, a story keeps its numbers on re-render."""
    used = [int(n) for n in re.findall(r"\bT(\d{3})\b", re.sub(rf"(?s)## Phase \d+: {re.escape(ref.story_id)}:.*?(?=^## Phase|\Z)",
                                                               "", existing or "", flags=re.MULTILINE))]
    counter = max(used or [0])
    numbers = numbers or {}
    lines = [f"## Phase {phase}: {ref.story_id}: {ref.story_title}", "",
             f"**Goal**: {ref.statement or ref.story_title}", "",
             "**Independent Test**: every acceptance criterion and edge case has a passing test case in "
             "[test_cases.md](test_cases.md)", ""]
    for task in plan.tasks:
        cases = [c for c in tests.cases if c.task_id == task.id]
        done = "x" if task.id in passed else " "
        lines.append(f"### {task.id}: {task.title}")
        if cases:
            counter += 1
            ids = ", ".join(numbers.get(c.id, c.id) for c in cases)
            files = ", ".join(sorted({f"`{c.target_file}`" for c in cases}))
            lines.append(f"- [{done}] T{counter:03d} [{ref.story_id}] Write tests {ids} in {files} (plan {task.id})")
        counter += 1
        files = ", ".join(f"`{p}`" for p in [*task.modify, *task.create])
        lines.append(f"- [{done}] T{counter:03d} [{ref.story_id}] {task.title} in {files} (plan {task.id})")
        lines.append("")
    lines.append(f"**Checkpoint**: {ref.story_id} is complete when every task above is ticked and validation passes.")
    return "\n".join(lines)


def tc_numbers(tests: TestPlan, existing: str | None, story_id: str) -> dict[str, str]:
    """File-wide TC numbers (TC-01, TC-02 ...) continuing after the other stories' cases."""
    others = re.sub(rf"(?s)^## {re.escape(story_id)}:.*?(?=^## [A-Z]{{2,4}}-US-|\Z)", "", existing or "",
                    flags=re.MULTILINE)
    start = max([int(n) for n in re.findall(r"^### TC-(\d+):", others, re.MULTILINE)] or [0])
    width = max(2, len(str(start + len(tests.cases))))
    return {case.id: f"TC-{start + index:0{width}d}" for index, case in enumerate(tests.cases, start=1)}


def test_cases_header(ref: FeatureRef, today: date | None = None) -> str:
    return (f"# Test Cases: {ref.feature_name} ({ref.prefix})\n\n"
            f"**Spec**: [spec.md](spec.md)  \n**Plan**: [plan.md](plan.md)  \n**Jira Epic**: TBD  \n"
            f"**Created**: {_today(today)}  \n**Updated**: {_today(today)} — {ref.story_id} test cases\n\n---")


def _label(cases) -> str:
    kinds = {c.type for c in cases}
    if "e2e" in kinds and kinds - {"e2e"}:
        return "BOTH"
    return "UI" if kinds == {"e2e"} else "API"


def test_cases_section(ref: FeatureRef, tests: TestPlan, criteria: list[Criterion], numbers: dict[str, str]) -> str:
    lines = [f"## {ref.story_id}: {ref.story_title} ({ref.actors})", "", "> **Jira Story:** TBD", "",
             "### Coverage Matrix", "", "| AC | Label | Integration TC(s) | E2E TC(s) |", "|---|---|---|---|"]
    for criterion in criteria:
        cases = [c for c in tests.cases if c.criterion == criterion.id]
        other = ", ".join(numbers[c.id] for c in cases if c.type != "e2e") or "—"
        e2e = ", ".join(numbers[c.id] for c in cases if c.type == "e2e")
        label = _label(cases) if cases else "API"
        if not e2e:
            e2e = "e2e N/A: covered below the interface" if label == "API" else "—"
        lines.append(f"| {criterion.id} — {_cell(criterion.text)} | {label} | {other} | {e2e} |")
    lines += ["", "### Test Cases", ""]
    for case in tests.cases:
        given, when, then = _split_scenario(case.description, case.expected)
        lines += [f"### {numbers[case.id]}: {case.name or _cell(case.description)[:60]}", "",
                  f"- **US**: {ref.story_id}", f"- **Given**: {given}", f"- **When**: {when}", f"- **Then**: {then}",
                  f"- **AC**: {case.criterion}", f"- **Type**: {case.type}", f"- **Severity**: {case.severity}",
                  f"- **File**: `{case.target_file}`", ""]
    return "\n".join(lines).rstrip()


def _split_scenario(description: str, expected: str) -> tuple[str, str, str]:
    """QA Analyst descriptions read 'given ..., when ..., then ...'; fall back to the whole text as When."""
    match = re.match(r"(?is)\s*given\s+(.*?)[,;]?\s+when\s+(.*?)(?:[,;]?\s+then\s+(.*))?$", description.strip())
    if match:
        return match[1].strip(" ,."), match[2].strip(" ,."), (match[3] or expected).strip(" ,.")
    return "—", description.strip(" ."), expected.strip(" .")
