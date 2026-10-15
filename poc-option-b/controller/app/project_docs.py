"""Project documents written before any feature run: the project brief and the SRS.

The SRS follows the company's SRS outline (sections 1-8 of the GRM SRS), so feature runs
can later append a story to section 7 and trace it to sections 2, 3 and 6. The Business
Analyst returns JSON; the controller numbers the ids and renders the Markdown, so the
format rules hold whatever the model writes.
"""

from datetime import date
import re

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from controller.app.schemas import Question

SRS_PATH = "SRS.md"
PREFIX = re.compile(r"[A-Z]{2,4}")
ROLE = re.compile(r"[A-Z][A-Z0-9_]{1,15}")
SHORTHAND_ACTORS = ("All", "All internal", "SYSTEM")
NFR_CATEGORIES = ("Performance", "Availability", "Scalability", "Security", "Privacy", "Reliability")
DEPENDENCY_KINDS = {"third_party": "8.1 Third-Party APIs", "internal": "8.2 Internal Systems / Legacy Services",
                    "infrastructure": "8.3 Infrastructure Dependencies"}
NOT_COVERED = "Not covered yet."


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------- project brief (the SRS run's input)

class ProjectBrief(Contract):
    title: str = Field(min_length=3, max_length=200)
    summary: str = Field(min_length=1)
    users: str = Field(min_length=1)
    features: str = Field(min_length=1)
    constraints: str = ""
    sources: str = ""


def render_brief(brief: ProjectBrief) -> str:
    sections = [("Summary", brief.summary), ("Users and roles", brief.users), ("Wanted features", brief.features),
                ("Constraints", brief.constraints), ("Sources", brief.sources)]
    body = "\n\n".join(f"## {heading}\n\n{text.strip() or '_None._'}" for heading, text in sections)
    return f"# Project brief: {brief.title}\n\n{body}\n"


# ---------------------------------------------------------------- SRS contract

class Term(Contract):
    term: str
    meaning: str


class Role(Contract):
    role: str
    description: str
    persona: str = ""

    @field_validator("role")
    @classmethod
    def _role(cls, value: str) -> str:
        value = value.strip().strip("`").upper().replace(" ", "_")
        if not ROLE.fullmatch(value):
            raise ValueError("a role is an upper-case code such as ADMIN or HR")
        return value


class Entity(Contract):
    name: str
    description: str
    attributes: str = ""
    lifecycle: str = ""


class Relationship(Contract):
    subject: str
    verb: str
    object: str
    type: str = "Association"
    cardinality: str = ""
    constraint: str = ""


class BusinessRule(Contract):
    id: str = ""
    statement: str
    entities: str = ""


class StateChange(Contract):
    source: str
    target: str
    trigger: str


class EntityStates(Contract):
    entity: str
    states: list[Term] = Field(min_length=1)
    changes: list[StateChange] = []


class Requirement(Contract):
    id: str = ""
    function: str
    requirement: str
    rules: str = ""


class Story(Contract):
    id: str = ""
    title: str = Field(min_length=3)
    actors: list[str] = Field(min_length=1)
    goal: str
    reason: str


class Feature(Contract):
    name: str = Field(min_length=2)
    prefix: str
    summary: str = ""
    requirements: list[Requirement] = []
    stories: list[Story] = Field(min_length=1)

    @field_validator("prefix")
    @classmethod
    def _prefix(cls, value: str) -> str:
        value = value.strip().upper()
        if not PREFIX.fullmatch(value):
            raise ValueError("a feature prefix is 2-4 upper-case letters, such as BU")
        return value


class Quality(Contract):
    category: str
    metric: str
    target: str

    @field_validator("category")
    @classmethod
    def _category(cls, value: str) -> str:
        match = [c for c in NFR_CATEGORIES if c.lower() == value.strip().lower()]
        if not match:
            raise ValueError(f"category must be one of {', '.join(NFR_CATEGORIES)}")
        return match[0]


class Flow(Contract):
    name: str
    steps: list[str] = Field(min_length=2)
    alternatives: list[str] = []
    negatives: list[str] = []


class Dependency(Contract):
    kind: str
    name: str
    purpose: str
    trigger: str = ""

    @field_validator("kind")
    @classmethod
    def _kind(cls, value: str) -> str:
        value = value.strip().lower().replace("-", "_").replace(" ", "_")
        if value not in DEPENDENCY_KINDS:
            raise ValueError(f"kind must be one of {', '.join(DEPENDENCY_KINDS)}")
        return value


class Srs(Contract):
    product: str = Field(min_length=2)
    purpose: str
    scope: str
    assumptions: list[str] = []
    definitions: list[Term] = []
    roles: list[Role] = Field(min_length=1)
    out_of_scope: list[str] = []
    entities: list[Entity] = Field(min_length=1)
    relationships: list[Relationship] = []
    business_rules: list[BusinessRule] = []
    states: list[EntityStates] = []
    features: list[Feature] = Field(min_length=1)
    qualities: list[Quality] = []
    experience: list[Term] = []
    flows: list[Flow] = Field(min_length=1)
    dependencies: list[Dependency] = []
    questions: list[Question] = []
    concerns: list[str] = []

    @model_validator(mode="after")
    def _consistent(self) -> "Srs":
        prefixes = [f.prefix for f in self.features]
        if len(prefixes) != len(set(prefixes)):
            raise ValueError("feature prefixes must be unique")
        known = {r.role for r in self.roles}
        for feature in self.features:
            for story in feature.stories:
                unknown = [a for a in story.actors if a not in SHORTHAND_ACTORS and a.strip("`").upper() not in known]
                if unknown:
                    raise ValueError(f"{feature.prefix} story '{story.title}' names actors {unknown} that are not "
                                     f"in roles ({', '.join(sorted(known))}) or {', '.join(SHORTHAND_ACTORS)}")
        return self


def number_srs(srs: Srs) -> Srs:
    """Give every rule, requirement and story its stable id: BR-01, BU-FR-01, BU-US-01. No gaps."""
    data = srs.model_dump()
    for index, rule in enumerate(data["business_rules"], start=1):
        rule["id"] = f"BR-{index:02d}"
    for feature in data["features"]:
        for index, requirement in enumerate(feature["requirements"], start=1):
            requirement["id"] = f"{feature['prefix']}-FR-{index:02d}"
        for index, story in enumerate(feature["stories"], start=1):
            story["id"] = f"{feature['prefix']}-US-{index:02d}"
            story["actors"] = [a if a in SHORTHAND_ACTORS else a.strip("`").upper() for a in story["actors"]]
    return Srs.model_validate(data)


# ---------------------------------------------------------------- rendering

def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def _cell(text: str) -> str:
    return " ".join(str(text).split()).replace("|", "\\|") or "—"


def _table(header: list[str], rows: list[list[str]]) -> list[str]:
    if not rows:
        return [NOT_COVERED]
    return ["| " + " | ".join(header) + " |", "|" + "---|" * len(header),
            *["| " + " | ".join(_cell(c) for c in row) + " |" for row in rows]]


def _bullets(items: list[str]) -> list[str]:
    return [f"- {item}" for item in items] or [NOT_COVERED]


def _actor_label(actors: list[str]) -> str:
    return ", ".join(actors)


def _actor_inline(actors: list[str]) -> str:
    return " or ".join(a if a in SHORTHAND_ACTORS else f"`{a}`" for a in actors)


def story_heading(section: str, story: Story) -> str:
    text = f"{section} {story.id}: {story.title} ({_actor_label(story.actors)})"
    return f"#### {text} <a id=\"{_slug(text)}\"></a>"


def story_statement(story: Story) -> str:
    return f"**As a** {_actor_inline(story.actors)}, **I want to** {story.goal} **so that** {story.reason}."


def render_srs(srs: Srs, *, author: str, today: date | None = None) -> str:
    """Markdown in the company SRS outline. Section numbers stay fixed so other documents can cite them."""
    srs = number_srs(srs)
    today = today or date.today()
    out: list[str] = []

    def heading(level: int, text: str) -> None:
        out.extend([f"{'#' * level} {text} <a id=\"{_slug(text)}\"></a>", ""])

    out += [f"# {srs.product} - Software Requirements Specification (SRS)", "",
            *_table(["Version", "Date", "Author", "Change"], [["0.1", today.isoformat(), author, "Initial draft"]]),
            "", "---", ""]
    out += ["## Table of Contents", ""]
    toc = ["1. Introduction", "2. Conceptual Domain Model", "3. Functional Requirements (FR)",
           "4. Non-Functional Requirements (NFR)", "5. User Experience Requirements (UXR)", "6. Business Flows",
           "7. Features and User Stories", "8. External Dependencies"]
    for item in toc:
        out.append(f"- [{item}](#{_slug(item)})")
        if item.startswith("7."):
            for f_index, feature in enumerate(srs.features, start=1):
                section = f"7.{f_index} {feature.name} ({feature.prefix})"
                out.append(f"  - [{section}](#{_slug(section)})")
                for s_index, story in enumerate(feature.stories, start=1):
                    text = f"7.{f_index}.{s_index} {story.id}: {story.title} ({_actor_label(story.actors)})"
                    out.append(f"    - [{text}](#{_slug(text)})")
    out += ["", "---", ""]

    heading(2, "1. Introduction")
    heading(3, "1.1 Purpose")
    out += [srs.purpose.strip(), ""]
    heading(3, "1.2 Scope")
    out += [srs.scope.strip(), ""]
    heading(3, "1.3 Assumptions and Constraints")
    out += [*_bullets(srs.assumptions), ""]
    heading(3, "1.4 Definitions and Acronyms")
    out += [*_table(["Term", "Definition"], [[d.term, d.meaning] for d in srs.definitions]), ""]
    heading(3, "1.5 Roles and Actors")
    out += [*_table(["Role", "Description", "Example persona"],
                    [[f"`{r.role}`", r.description, r.persona] for r in srs.roles]), ""]
    heading(3, "1.6 Out of Scope")
    out += [*_bullets(srs.out_of_scope), ""]

    heading(2, "2. Conceptual Domain Model")
    heading(3, "2.1 Domain Entity")
    out += [*_table(["Entity", "Description", "Key Business Attributes", "Ownership / Lifecycle"],
                    [[e.name, e.description, e.attributes, e.lifecycle] for e in srs.entities]), ""]
    heading(3, "2.2 Entity Relationship and Cardinality")
    out += [*_table(["Subject", "Verb Phrase", "Object", "Type", "Cardinality", "Constraint"],
                    [[r.subject, r.verb, r.object, r.type, r.cardinality, r.constraint]
                     for r in srs.relationships]), ""]
    heading(3, "2.3 Business Rules")
    out += [*_table(["Rule ID", "Statement", "Entities Involved"],
                    [[b.id, b.statement, b.entities] for b in srs.business_rules]), ""]
    heading(3, "2.4 Domain Object State")
    if not srs.states:
        out += [NOT_COVERED, ""]
    for index, entity in enumerate(srs.states, start=1):
        heading(4, f"2.4.{index} {entity.entity} State")
        if entity.changes:
            out += ["```mermaid", "stateDiagram-v2",
                    *[f"    {c.source} --> {c.target} : {c.trigger}" for c in entity.changes], "```", ""]
        out += [*_table(["State", "Meaning"], [[f"`{s.term}`", s.meaning] for s in entity.states]), ""]

    heading(2, "3. Functional Requirements (FR)")
    for index, feature in enumerate(srs.features, start=1):
        heading(3, f"3.{index} {feature.name}")
        if feature.summary:
            out += [feature.summary.strip(), ""]
        out += [*_table(["No.", "Function", "Requirement", "Business Rules"],
                        [[r.id, r.function, r.requirement, r.rules] for r in feature.requirements]), ""]

    heading(2, "4. Non-Functional Requirements (NFR)")
    for index, category in enumerate(NFR_CATEGORIES, start=1):
        heading(3, f"4.{index} {category}")
        out += [*_table(["Metric", "Target"], [[q.metric, q.target] for q in srs.qualities
                                                if q.category == category]), ""]

    heading(2, "5. User Experience Requirements (UXR)")
    if not srs.experience:
        out += [NOT_COVERED, ""]
    for index, item in enumerate(srs.experience, start=1):
        heading(3, f"5.{index} {item.term}")
        out += [item.meaning.strip(), ""]

    heading(2, "6. Business Flows")
    for index, flow in enumerate(srs.flows, start=1):
        heading(3, f"6.{index} {flow.name}")
        out += [*[f"{n}. {step}" for n, step in enumerate(flow.steps, start=1)], ""]
        out += ["#### Alternative Flows", "", *_bullets(flow.alternatives), ""]
        out += ["#### Negative Flows", "", *_bullets(flow.negatives), ""]

    heading(2, "7. Features and User Stories")
    for f_index, feature in enumerate(srs.features, start=1):
        heading(3, f"7.{f_index} {feature.name} ({feature.prefix})")
        for s_index, story in enumerate(feature.stories, start=1):
            out += [story_heading(f"7.{f_index}.{s_index}", story), "", story_statement(story), ""]

    heading(2, "8. External Dependencies")
    for kind, title in DEPENDENCY_KINDS.items():
        heading(3, title)
        out += [*_table(["Name", "Purpose", "Trigger"], [[d.name, d.purpose, d.trigger]
                                                          for d in srs.dependencies if d.kind == kind]), ""]
    return "\n".join(out).rstrip() + "\n"


# ---------------------------------------------------------------- checks on a human-edited SRS

REQUIRED_SECTIONS = ("1. Introduction", "2. Conceptual Domain Model", "3. Functional Requirements",
                     "4. Non-Functional Requirements", "5. User Experience Requirements", "6. Business Flows",
                     "7. Features and User Stories", "8. External Dependencies")
STORY_LINE = re.compile(r"^####\s+7\.\d+\.\d+\s+(?:\[[^\]]+\]\([^)]*\):\s+)?([A-Z]{2,4}-US-\d{2}(?:\.\d+)?):",
                        re.MULTILINE)


def check_srs_markdown(content: str) -> list[str]:
    """Problems that would break later steps: missing sections, no stories, duplicate story ids."""
    problems = []
    headings = [line for line in content.splitlines() if line.startswith("## ")]
    for section in REQUIRED_SECTIONS:
        if not any(line[3:].lstrip().startswith(section) for line in headings):
            problems.append(f"section '{section}' is missing")
    stories = STORY_LINE.findall(content)
    if not stories:
        problems.append("section 7 lists no user story headings like '#### 7.1.1 AT-US-01: Title (ROLE)'")
    duplicates = sorted({s for s in stories if stories.count(s) > 1})
    if duplicates:
        problems.append(f"user story ids appear twice: {', '.join(duplicates)}")
    return problems


def srs_stories(content: str) -> list[str]:
    return STORY_LINE.findall(content)
