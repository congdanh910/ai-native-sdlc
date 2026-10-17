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
    """The SRS run's input: a brief typed in, or the discovery baseline merged into the repository."""
    title: str = Field(min_length=3, max_length=200)
    summary: str = ""
    users: str = ""
    features: str = ""
    constraints: str = ""
    sources: str = ""
    from_baseline: bool = False  # build on requirements/baseline.json of the default branch

    @model_validator(mode="after")
    def _filled(self) -> "ProjectBrief":
        if not self.from_baseline and not (self.summary.strip() and self.users.strip() and self.features.strip()):
            raise ValueError("a brief needs a summary, users and wanted features, or build on the discovery baseline")
        return self


def render_brief(brief: ProjectBrief) -> str:
    if brief.from_baseline:
        notes = "\n\n".join(f"## {h}\n\n{t.strip()}" for h, t in (("Notes", brief.summary), ("Constraints", brief.constraints))
                            if t.strip())
        return (f"# Project brief: {brief.title}\n\nThe SRS is built on the discovery baseline "
                f"(requirements/baseline.json on the default branch).\n" + (f"\n{notes}\n" if notes else ""))
    sections = [("Summary", brief.summary), ("Users and roles", brief.users), ("Wanted features", brief.features),
                ("Constraints", brief.constraints), ("Sources", brief.sources)]
    body = "\n\n".join(f"## {heading}\n\n{text.strip() or '_None._'}" for heading, text in sections)
    return f"# Project brief: {brief.title}\n\n{body}\n"


# ---------------------------------------------------------------- SRS contract

class Term(Contract):
    term: str
    meaning: str
    refs: list[str] = []  # baseline REQ ids this element comes from (an SRS built on a discovery baseline)


class Role(Contract):
    role: str
    description: str
    persona: str = ""
    refs: list[str] = []  # baseline REQ ids this element comes from (an SRS built on a discovery baseline)

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
    refs: list[str] = []  # baseline REQ ids this element comes from (an SRS built on a discovery baseline)


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
    refs: list[str] = []  # baseline REQ ids this element comes from (an SRS built on a discovery baseline)


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
    refs: list[str] = []  # baseline REQ ids this element comes from (an SRS built on a discovery baseline)


class Story(Contract):
    id: str = ""
    title: str = Field(min_length=3)
    actors: list[str] = Field(min_length=1)
    goal: str
    reason: str
    refs: list[str] = []  # baseline REQ ids this element comes from (an SRS built on a discovery baseline)


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
    refs: list[str] = []  # baseline REQ ids this element comes from (an SRS built on a discovery baseline)

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
    refs: list[str] = []  # baseline REQ ids this element comes from (an SRS built on a discovery baseline)


class Dependency(Contract):
    kind: str
    name: str
    purpose: str
    trigger: str = ""
    refs: list[str] = []  # baseline REQ ids this element comes from (an SRS built on a discovery baseline)

    @field_validator("kind")
    @classmethod
    def _kind(cls, value: str) -> str:
        value = value.strip().lower().replace("-", "_").replace(" ", "_")
        if value not in DEPENDENCY_KINDS:
            raise ValueError(f"kind must be one of {', '.join(DEPENDENCY_KINDS)}")
        return value


class TraceNote(Contract):
    """A baseline item the SRS covers in prose (purpose, scope, assumptions, out of scope, experience)."""
    req: str
    section: str  # such as "1.2 Scope"
    note: str = ""


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
    trace_notes: list[TraceNote] = []

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


SHORTHAND_WORDS = {"All": "any signed-in user", "All internal": "any internal user", "SYSTEM": "`SYSTEM`"}


def _actor_inline(actors: list[str]) -> str:
    """'**As a** `HR`', or plain words for the shorthands: '**As** any internal user'."""
    return " or ".join(SHORTHAND_WORDS.get(a, f"`{a}`") for a in actors)


def story_heading(section: str, story: Story) -> str:
    text = f"{section} {story.id}: {story.title} ({_actor_label(story.actors)})"
    return f"#### {text} <a id=\"{_slug(text)}\"></a>"


def story_statement(story: Story) -> str:
    lead = "**As**" if story.actors[0] in ("All", "All internal") else "**As a**"
    return f"{lead} {_actor_inline(story.actors)}, **I want to** {story.goal} **so that** {story.reason}."


TRACE_HEADING = "Appendix A. Requirement traceability"
NOT_IN_SRS = ("option",)  # architecture options go to the SDS Options step


def srs_trace(srs: Srs) -> dict[str, list[str]]:
    """REQ id -> where the SRS uses it, by the ids and names the SRS shows."""
    srs = number_srs(srs)
    where: dict[str, list[str]] = {}

    def add(refs: list[str], label: str) -> None:
        for ref in refs:
            where.setdefault(ref.strip(), []).append(label)
    for term in srs.definitions:
        add(term.refs, f"1.4 {term.term}")
    for role in srs.roles:
        add(role.refs, f"1.5 {role.role}")
    for entity in srs.entities:
        add(entity.refs, f"2 {entity.name}")
    for rule in srs.business_rules:
        add(rule.refs, rule.id)
    for feature in srs.features:
        for requirement in feature.requirements:
            add(requirement.refs, requirement.id)
        for story in feature.stories:
            add(story.refs, story.id)
    for quality in srs.qualities:
        add(quality.refs, f"4 {quality.category}: {quality.metric}")
    for term in srs.experience:
        add(term.refs, f"5 {term.term}")
    for flow in srs.flows:
        add(flow.refs, f"6 {flow.name}")
    for dependency in srs.dependencies:
        add(dependency.refs, f"8 {dependency.name}")
    for note in srs.trace_notes:
        add([note.req], note.section + (f" ({note.note})" if note.note else ""))
    return where


def render_trace(srs: Srs, baseline: dict) -> list[str]:
    where = srs_trace(srs)
    known = {i["id"]: i for i in baseline.get("items", [])}
    out = {i["id"]: i for i in baseline.get("out_of_scope", [])}
    rows = [[i["id"], i["type"], i["name"], "; ".join(where.get(i["id"], [])) or "—"]
            for i in baseline.get("items", []) if i["type"] not in NOT_IN_SRS]
    rows += [[ref, f"{out[ref]['type']} (out of scope)", out[ref]["name"], "; ".join(labels)]
             for ref, labels in where.items() if ref in out]
    rows += [[ref, "?", "not in the baseline", "; ".join(labels)] for ref, labels in where.items()
             if ref not in known and ref not in out]
    return [f"## {TRACE_HEADING} <a id=\"{_slug(TRACE_HEADING)}\"></a>", "",
            f"Built on the requirement baseline (inventory v{baseline.get('inventory_version', '?')}, requirements/baseline.json). "
            "Every item in scope is used at least once; architecture options go to the SDS.", "",
            *_table(["REQ", "Type", "Name", "In this SRS"], rows), ""]


def check_srs_trace(content: str, baseline: dict) -> list[str]:
    """Every baseline item in scope (but options) has a row with a place in the SRS; no row cites an unknown REQ."""
    if f"## {TRACE_HEADING}" not in content:
        return [f"'{TRACE_HEADING}' is missing"]
    section = content.split(f"## {TRACE_HEADING}", 1)[1]
    rows = {}
    for line in section.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) >= 4 and re.fullmatch(r"REQ-\d+", cells[0]):
            rows[cells[0]] = cells
    problems = []
    unused = [i["id"] for i in baseline.get("items", []) if i["type"] not in NOT_IN_SRS
              and rows.get(i["id"], ["", "", "", "—"])[3] in ("", "—")]
    if unused:
        problems.append(f"{len(unused)} baseline item(s) are not used by any FR, story or section: {', '.join(unused[:12])}")
    unknown = [r for r, cells in rows.items() if cells[1] == "?"]
    if unknown:
        problems.append(f"the SRS cites REQ ids that are not in the baseline: {', '.join(unknown[:12])}")
    return problems


def render_srs(srs: Srs, *, author: str, today: date | None = None, baseline: dict | None = None) -> str:
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
    if baseline:
        out += render_trace(srs, baseline)
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


# ---------------------------------------------------------------- reading an SRS back

FEATURE_LINE = re.compile(r"^###\s+7\.(\d+)\s+(.+?)\s+\(([A-Z]{2,4})\)", re.MULTILINE)
STORY_HEADING = re.compile(r"^####\s+7\.(\d+)\.(\d+)\s+(?:\[[^\]]+\]\([^)]*\):\s+)?([A-Z]{2,4}-US-\d{2}(?:\.\d+)?):\s+"
                           r"(.+?)\s+\(([^)]*)\)", re.MULTILINE)
TAGS = re.compile(r"</?[A-Za-z][^<>\n]*>")  # HTML tags only; text such as "< 300 ms" or "->" stays


class SrsStory(Contract):
    id: str
    section: str
    title: str
    actors: str
    statement: str = ""


class SrsFeature(Contract):
    section: str
    name: str
    prefix: str
    stories: list[SrsStory] = []


def srs_features(content: str) -> list[SrsFeature]:
    """Section 7 of an SRS as features and stories. Works on rendered SRS files and on the GRM style (spans, emoji)."""
    text = TAGS.sub("", content)
    seven = re.search(r"^##\s+7\.\s.*?$(.*?)(?=^##\s+\d+\.\s|\Z)", text, re.MULTILINE | re.DOTALL)
    if not seven:
        return []
    body = seven[1]
    features: list[SrsFeature] = []
    for match in FEATURE_LINE.finditer(body):
        name = re.sub(r"[^\w\s&/()-]+", "", match[2]).strip()
        features.append(SrsFeature(section=f"7.{match[1]}", name=name, prefix=match[3]))
    stories = list(STORY_HEADING.finditer(body))
    for index, match in enumerate(stories):
        end = stories[index + 1].start() if index + 1 < len(stories) else len(body)
        following = body[match.end():end]
        nxt = re.search(r"^#{2,3}\s", following, re.MULTILINE)
        following = following[:nxt.start()] if nxt else following
        statement = next((line.strip() for line in following.splitlines() if line.strip().startswith("**As a")), "")
        story = SrsStory(id=match[3], section=f"7.{match[1]}.{match[2]}", title=match[4].strip(),
                         actors=match[5].strip(), statement=statement)
        feature = next((f for f in features if f.section == f"7.{match[1]}"), None)
        if feature:
            feature.stories.append(story)
    return features


# ---------------------------------------------------------------- SDS (architecture) run

SDS_PATH = "SDS.md"
API_ID = re.compile(r"API-[A-Z]{2,4}-\d{2}")


class ArchitectureBrief(Contract):
    """The SDS run's input. With propose_stack the Architect proposes the stack, deployment and integrations in the
    Options step from the SRS, the baseline and the repository; what the brief fills in is a constraint it follows."""
    title: str = Field(min_length=3, max_length=200)
    stack: str = ""
    deployment: str = ""
    integrations: str = ""
    notes: str = ""
    propose_stack: bool = False

    @model_validator(mode="after")
    def _stack(self) -> "ArchitectureBrief":
        if not self.propose_stack and not self.stack.strip():
            raise ValueError("name the technology and stack, or let the Architect propose it")
        return self


def render_architecture_brief(brief: ArchitectureBrief) -> str:
    sections = [("Technology and stack", brief.stack), ("Deployment and infrastructure", brief.deployment),
                ("Integrations", brief.integrations), ("Notes", brief.notes)]
    if brief.propose_stack:
        lead = ("The Architect proposes the stack, deployment and integrations in the Options step, from the SRS, the "
                "requirement baseline and the repository. What this brief states is a constraint the options follow.\n\n")
        body = "\n\n".join(f"## {heading} (constraint)\n\n{text.strip()}" for heading, text in sections if text.strip())
        return f"# Architecture brief: {brief.title}\n\n{lead}{body}\n".rstrip() + "\n"
    body = "\n\n".join(f"## {heading}\n\n{text.strip() or '_None._'}" for heading, text in sections)
    return f"# Architecture brief: {brief.title}\n\n{body}\n"


class Trace(Contract):
    entity: str = "N/A"  # SRS §2 entity, or N/A for a technical-only object
    object: str
    table: str = ""
    notes: str = ""


class DomainObject(Contract):
    name: str
    description: str
    notes: str = ""


class Screen(Contract):
    name: str
    roles: str
    purpose: str


class Component(Contract):
    name: str
    responsibility: str
    technology: str = ""


class Table(Contract):
    name: str
    columns: str
    notes: str = ""


class Node(Contract):
    name: str
    runs: str


class Scenario(Contract):
    name: str
    steps: list[str] = Field(min_length=1)


class Endpoint(Contract):
    method: str
    path: str
    feature: str
    stories: list[str] = []
    purpose: str = ""
    id: str = ""


class ErrorCode(Contract):
    http: int
    code: str
    reason: str
    resolution: str = ""


class Threat(Contract):
    threat: str
    mitigation: str


class Approach(Contract):
    nfr: str
    approach: str
    verification: str = ""
    target: str = ""


class Decision(Contract):
    title: str
    context: str
    options: list[str] = []
    decision: str
    consequences: str = ""


class Integration(Contract):
    name: str
    purpose: str
    interface: str = ""


class Sds(Contract):
    purpose: str
    scope: str
    assumptions: list[str] = []
    traceability: list[Trace] = Field(min_length=1)
    objects: list[DomainObject] = []
    ui_principles: list[str] = []
    screens: list[Screen] = []
    context: str
    components: list[Component] = Field(min_length=1)
    stack: list[Term] = Field(min_length=1)  # layer -> technology
    modules: list[Term] = []  # repository path -> purpose
    tables: list[Table] = []
    process: list[Scenario] = []
    deployment: list[Node] = []
    principles: list[str] = []
    purposes: dict[str, str] = {}  # SRS story id -> its Purpose for §5
    api_standards: list[str] = []
    endpoints: list[Endpoint] = []
    errors: list[ErrorCode] = []
    authentication: str = ""
    authorization: list[Term] = []  # role -> what it may do
    threats: list[Threat] = []
    data_protection: list[str] = []
    secrets: list[str] = []
    nfr: list[Approach] = []
    decisions: list[Decision] = Field(min_length=1)
    metrics: list[Term] = []
    future: list[str] = []
    conventions: list[Term] = []
    integrations: list[Integration] = []
    questions: list[Question] = []
    concerns: list[str] = []


def number_sds(sds: Sds, features: list[SrsFeature]) -> Sds:
    """API ids per feature (API-BU-01), and only purposes for stories the SRS has."""
    data = sds.model_dump()
    counters: dict[str, int] = {}
    prefixes = {f.prefix for f in features}
    for endpoint in data["endpoints"]:
        feature = endpoint["feature"].strip().upper()
        endpoint["feature"] = feature
        counters[feature] = counters.get(feature, 0) + 1
        endpoint["id"] = f"API-{feature}-{counters[feature]:02d}" if feature in prefixes else f"API-{counters[feature]:02d}"
    known = {s.id for f in features for s in f.stories}
    data["purposes"] = {k: v for k, v in data["purposes"].items() if k in known}
    return Sds.model_validate(data)


PURPOSE_LATER = "_Purpose to be written by the Design step of this story's run._"


def render_sds(sds: Sds, features: list[SrsFeature], *, product: str, author: str, today: date | None = None) -> str:
    """Markdown in the company SDS outline (sections 1-13); section 5 mirrors SRS section 7 story by story."""
    sds = number_sds(sds, features)
    today = today or date.today()
    out: list[str] = []

    def heading(level: int, text: str) -> None:
        out.extend([f"{'#' * level} {text} <a id=\"{_slug(text)}\"></a>", ""])

    toc = ["1. Introduction", "2. Technical Domain Model", "3. UI Design", "4. Architecture Design",
           "5. Product Features and User Story Specification", "6. API Design", "7. Security Design",
           "8. Non-Functional Requirements (NFR)", "9. Design Decisions and Tradeoffs", "10. Product Metrics",
           "11. Future Enhancements", "12. Appendix", "13. External Integrations"]
    out += [f"# {product} - Software Design Specification (SDS)", "",
            *_table(["Version", "Date", "Author", "Change"], [["0.1", today.isoformat(), author, "Initial draft"]]),
            "", "---", "", "## Table of Contents", ""]
    for item in toc:
        out.append(f"- [{item}](#{_slug(item)})")
        if item.startswith("5."):
            for index, feature in enumerate(features, start=1):
                section = f"5.{index} {feature.name} ({feature.prefix})"
                out.append(f"  - [{section}](#{_slug(section)})")
                for s_index, story in enumerate(feature.stories, start=1):
                    text = f"5.{index}.{s_index} {story.id}: {story.title} ({story.actors})"
                    out.append(f"    - [{text}](#{_slug(text)})")
    out += ["", "---", ""]

    heading(2, "1. Introduction")
    heading(3, "1.1 Purpose")
    out += [sds.purpose.strip(), ""]
    heading(3, "1.2 Scope")
    out += [sds.scope.strip(), ""]
    heading(3, "1.3 Assumptions and Constraints")
    out += [*_bullets(sds.assumptions), ""]
    heading(3, "1.4 Related Documents")
    out += [f"- [SRS.md]({SRS_PATH}): requirements this design implements", ""]

    heading(2, "2. Technical Domain Model")
    heading(3, "2.1 Domain Layer Traceability")
    out += [f"> **Last synced with SRS §2.1 Domain Entity:** {today.isoformat()}", "",
            *_table(["SRS §2.1 Domain Entity", "SDS §2.2 Domain Object", "SDS §4.3.3 Database Entity", "Notes"],
                    [[f"`{t.entity}`" if t.entity != "N/A" else "N/A", f"`{t.object}`",
                      f"`{t.table}`" if t.table else "", t.notes] for t in sds.traceability]), ""]
    heading(3, "2.2 Domain Object")
    out += [*_table(["Object", "Description", "Implementation Notes"],
                    [[o.name, o.description, o.notes] for o in sds.objects]), ""]
    heading(3, "2.3 Domain Object State")
    out += [f"States and transitions follow [SRS §2.4](SRS.md#2-4-domain-object-state).", ""]

    heading(2, "3. UI Design")
    heading(3, "3.1 UI/UX Principles")
    out += [*_bullets(sds.ui_principles), ""]
    heading(3, "3.2 Screens")
    out += [*_table(["Screen", "Roles", "Purpose"], [[s.name, s.roles, s.purpose] for s in sds.screens]), ""]

    heading(2, "4. Architecture Design")
    heading(3, "4.1 System Context")
    out += [sds.context.strip(), ""]
    heading(3, "4.2 Logical View")
    out += [*_table(["Component", "Responsibility", "Technology"],
                    [[c.name, c.responsibility, c.technology] for c in sds.components]), ""]
    heading(3, "4.3 Development View")
    heading(4, "4.3.1 Technology Stack")
    out += [*_table(["Layer", "Technology"], [[t.term, t.meaning] for t in sds.stack]), ""]
    heading(4, "4.3.2 Repository Layout")
    out += [*_table(["Path", "Purpose"], [[f"`{m.term}`", m.meaning] for m in sds.modules]), ""]
    heading(4, "4.3.3 Database Schema")
    out += [*_table(["Table", "Columns", "Notes"], [[f"`{t.name}`", t.columns, t.notes] for t in sds.tables]), ""]
    heading(3, "4.4 Process View")
    if not sds.process:
        out += [NOT_COVERED, ""]
    for index, scenario in enumerate(sds.process, start=1):
        heading(4, f"4.4.{index} {scenario.name}")
        out += [*[f"{n}. {step}" for n, step in enumerate(scenario.steps, start=1)], ""]
    heading(3, "4.5 Physical View")
    out += [*_table(["Node", "Runs"], [[n.name, n.runs] for n in sds.deployment]), ""]
    heading(3, "4.6 Architecture Principles")
    out += [*_bullets(sds.principles), ""]

    heading(2, "5. Product Features and User Story Specification")
    out += [f"> **Last synced with SRS §7 User Stories:** {today.isoformat()}", "",
            "Each story keeps only its Purpose here; the Design step of its run links the plan once on the feature.", ""]
    for index, feature in enumerate(features, start=1):
        heading(3, f"5.{index} {feature.name} ({feature.prefix})")
        for s_index, story in enumerate(feature.stories, start=1):
            heading(4, f"5.{index}.{s_index} {story.id}: {story.title} ({story.actors})")
            out += ["**Purpose**", "", sds.purposes.get(story.id, PURPOSE_LATER), ""]

    heading(2, "6. API Design")
    heading(3, "6.1 API Design Standards")
    out += [*_bullets(sds.api_standards), ""]
    heading(3, "6.2 Data Transfer Objects (DTOs) and Domain Mapping")
    out += ["_Added by the Design step of each story's run._", ""]
    heading(3, "6.3 API Index")
    out += [*_table(["API ID", "Endpoint", "Feature"],
                    [[f"`{e.id}`", f"`{e.method.upper()} {e.path}`", e.feature] for e in sds.endpoints]), ""]
    heading(3, "6.4 API Specification")
    out += ["_Added by the Design step of each story's run._", ""]
    heading(3, "6.5 API -> User Story Traceability")
    out += [*_table(["API ID", "User Stories", "Purpose"],
                    [[f"`{e.id}`", ", ".join(e.stories), e.purpose] for e in sds.endpoints]), ""]
    heading(3, "6.6 Error Response Catalog")
    out += [*_table(["HTTP", "Error Code", "Domain/Business Reason", "Resolution"],
                    [[str(e.http), f"`{e.code}`", e.reason, e.resolution] for e in sds.errors]), ""]

    heading(2, "7. Security Design")
    heading(3, "7.1 User Authentication")
    out += [sds.authentication.strip() or NOT_COVERED, ""]
    heading(3, "7.2 User Authorization")
    out += [*_table(["Role", "Allowed"], [[f"`{a.term}`", a.meaning] for a in sds.authorization]), ""]
    heading(3, "7.3 Threat Modeling")
    out += [*_table(["Threat", "Mitigation"], [[t.threat, t.mitigation] for t in sds.threats]), ""]
    heading(3, "7.4 Data Protection")
    out += [*_bullets(sds.data_protection), ""]
    heading(3, "7.5 Secret Management")
    out += [*_bullets(sds.secrets), ""]

    heading(2, "8. Non-Functional Requirements (NFR)")
    out += [*_table(["NFR", "Design Approach"], [[a.nfr, a.approach] for a in sds.nfr]), ""]
    heading(3, "8.1 NFR Verification Matrix")
    out += [*_table(["NFR", "Verification Method", "Target"], [[a.nfr, a.verification, a.target] for a in sds.nfr]), ""]

    heading(2, "9. Design Decisions and Tradeoffs")
    for index, decision in enumerate(sds.decisions, start=1):
        heading(3, f"9.{index} {decision.title}")
        out += [f"**Context:** {decision.context.strip()}", ""]
        if decision.options:
            out += ["**Options considered:**", "", *[f"- {o}" for o in decision.options], ""]
        out += [f"**Decision:** {decision.decision.strip()}", ""]
        if decision.consequences:
            out += [f"**Consequences:** {decision.consequences.strip()}", ""]

    heading(2, "10. Product Metrics")
    out += [*_table(["Metric", "Definition"], [[m.term, m.meaning] for m in sds.metrics]), ""]
    heading(2, "11. Future Enhancements")
    out += [*_bullets(sds.future), ""]
    heading(2, "12. Appendix")
    heading(3, "12.1 Naming Conventions")
    out += [*_table(["Item", "Convention"], [[c.term, c.meaning] for c in sds.conventions]), ""]
    heading(2, "13. External Integrations")
    out += [*_table(["Integration", "Purpose", "Interface"],
                    [[i.name, i.purpose, i.interface] for i in sds.integrations]), ""]
    return "\n".join(out).rstrip() + "\n"


SDS_SECTIONS = ("1. Introduction", "2. Technical Domain Model", "4. Architecture Design",
                "5. Product Features and User Story Specification", "6. API Design", "9. Design Decisions")
SDS_STORY_LINE = re.compile(r"^####\s+5\.\d+\.\d+\s+([A-Z]{2,4}-US-\d{2}(?:\.\d+)?):", re.MULTILINE)


def check_sds_markdown(content: str, srs: str | None = None) -> list[str]:
    problems = []
    text = TAGS.sub("", content)
    headings = [line for line in text.splitlines() if line.startswith("## ")]
    for section in SDS_SECTIONS:
        if not any(line[3:].lstrip().startswith(section) for line in headings):
            problems.append(f"section '{section}' is missing")
    if srs:
        expected = [s.id for f in srs_features(srs) for s in f.stories]
        found = SDS_STORY_LINE.findall(text)
        missing = [s for s in expected if s not in found]
        if missing:
            problems.append(f"section 5 has no entry for SRS stories {', '.join(missing)}")
    return problems


def sds_sync_findings(sds: Sds, srs: str) -> list[str]:
    """Where the design does not cover the requirements: SRS entities without a traced object, stories without
    a purpose. Shown at the SDS gate; they do not block it."""
    findings = []
    entities = srs_entities(srs)
    traced = {t.entity.strip("`") for t in sds.traceability}
    for entity in entities:
        if entity not in traced:
            findings.append(f"SRS entity {entity} has no row in SDS §2.1 traceability")
    for feature in srs_features(srs):
        for story in feature.stories:
            if story.id not in sds.purposes:
                findings.append(f"{story.id} has no Purpose in SDS §5 yet")
    return findings


def srs_entities(srs: str) -> list[str]:
    """Entity names from the SRS "Domain Entity" table (section 2.1 here, 2.2 in GRM), not from state tables."""
    section = _section_text(srs, "2.")
    match = re.search(r"^###\s+2\.\d+\s+Domain Entit\w*.*?$(.*?)(?=^###\s|\Z)", section, re.MULTILINE | re.DOTALL)
    rows = re.findall(r"^\|\s*`?([A-Z][A-Za-z0-9]+)`?\s*\|", match[1] if match else "", re.MULTILINE)
    return list(dict.fromkeys(r for r in rows if r != "Entity"))


def _section_text(markdown: str, number: str) -> str:
    text = TAGS.sub("", markdown)
    match = re.search(rf"^##\s+{re.escape(number)}\s.*?$(.*?)(?=^##\s+\d+\.\s|\Z)", text, re.MULTILINE | re.DOTALL)
    return match[1] if match else ""


# ---------------------------------------------------------------- architecture options (the SDS run's first step)

class StackChoice(Contract):
    layer: str  # frontend, backend, database, AI / ML, messaging, hosting ...
    technology: str
    why: str = ""


class ArchOption(Contract):
    id: str = ""  # PA1, PA2 ... assigned by the controller
    name: str = Field(min_length=2)
    summary: str
    refs: list[str] = []  # baseline option items it comes from; empty when the Architect proposes it
    stack: list[StackChoice] = []  # what each layer runs on, and why
    deployment: str = ""  # where it runs: environments, hosting, devices
    integrations: str = ""  # how it connects to the other systems
    nfr_fit: str = ""  # how it meets the quality targets
    cost: str = ""
    risks: str = ""
    personal_data: str = ""
    operations: str = ""
    pros: list[str] = []
    cons: list[str] = []


class Options(Contract):
    context: str  # what has to be decided and why it matters
    options: list[ArchOption] = Field(min_length=2)
    recommendation: str  # the id or name of the option the Architect recommends
    reason: str
    questions: list[Question] = []


def number_options(options: Options) -> Options:
    data = options.model_dump()
    for index, option in enumerate(data["options"], start=1):
        option["id"] = f"PA{index}"
    names = {o["name"].lower(): o["id"] for o in data["options"]}
    data["recommendation"] = names.get(data["recommendation"].strip().lower(), data["recommendation"].strip())
    return Options.model_validate(data)


def render_options(options: Options, *, title: str) -> str:
    options = number_options(options)
    rows = [("Summary", "summary"), ("Deployment", "deployment"), ("Integrations", "integrations"),
            ("Quality targets", "nfr_fit"), ("Cost", "cost"), ("Risks", "risks"),
            ("Personal data", "personal_data"), ("Operations", "operations")]
    out = [f"# Architecture options: {title}", "", options.context.strip(), "",
           *_table(["", *[f"{o.id} {o.name}" for o in options.options]],
                   [[label, *[getattr(o, key) or "—" for o in options.options]] for label, key in rows]
                   + [["Pros", *["; ".join(o.pros) or "—" for o in options.options]],
                      ["Cons", *["; ".join(o.cons) or "—" for o in options.options]],
                      ["From the baseline", *[", ".join(o.refs) or "proposed by the Architect" for o in options.options]]]),
           "", f"**Recommendation:** {options.recommendation}. {options.reason.strip()}", ""]
    for option in options.options:
        if option.stack:
            out += [f"### {option.id} {option.name}: stack", "",
                    *_table(["Layer", "Technology", "Why"], [[c.layer, c.technology, c.why] for c in option.stack]), ""]
    return "\n".join(out)


def check_options(options: Options, baseline: dict | None) -> list[str]:
    """Every option item of the baseline is compared; the recommendation is one of the options."""
    options = number_options(options)
    problems = []
    wanted = [i["id"] for i in (baseline or {}).get("items", []) if i["type"] == "option"]
    used = {r for o in options.options for r in o.refs}
    missing = [w for w in wanted if w not in used]
    if missing:
        problems.append(f"baseline options not compared: {', '.join(missing)}")
    if options.recommendation not in {o.id for o in options.options}:
        problems.append(f"the recommendation {options.recommendation!r} is not one of the options")
    return problems


def check_chosen_option(content: str, chosen: dict) -> list[str]:
    """A decision of SDS section 9 names the option the Solution Architect chose (not only its list of options)."""
    match = re.search(r"^## 9\. Design Decisions.*?(?=^## |\Z)", content, re.MULTILINE | re.DOTALL)
    decided = " ".join(line for line in (match[0] if match else "").splitlines() if line.startswith("**Decision:**"))
    if chosen["name"].lower() not in decided.lower() and chosen["id"] not in decided:
        return [f"section 9 does not record the chosen option {chosen['id']} {chosen['name']}"]
    return []
