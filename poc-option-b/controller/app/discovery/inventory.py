"""The requirement inventory: one schema for every project, every item citing where it came from.

Extraction runs per chunk of a source (a range of PDF pages, or a slice of a text view); the controller merges
the chunks, numbers the items REQ-001, REQ-002 ... and applies the Business Analyst's consolidation (synonyms
merged, conflicts and gaps turned into questions). Nothing here knows a domain: types, fields and question kinds
are the same for a camera system, an OCR tool or a payroll project.
"""

import re

from pydantic import BaseModel, ConfigDict, Field, field_validator

ITEM_TYPES = ("goal", "actor", "permission", "feature", "function", "flow", "rule", "data", "integration", "exception",
              "acceptance", "nfr", "constraint", "option", "term")
QUESTION_KINDS = ("scope", "actor", "permission", "rule", "threshold", "data", "integration", "nfr", "exception",
                  "acceptance", "compliance", "deployment", "conflict", "missing_source", "customer")


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Citation(Contract):
    source: str  # source id
    location: str  # "page 7", "slide 4", "p12", "Sheet1!B3"


class Item(Contract):
    id: str = ""  # REQ-001, assigned by the controller
    type: str
    name: str = Field(min_length=1, max_length=200)  # short label: the actor, feature, rule ... as one line
    text: str = ""  # the requirement in the source's meaning, without inventing
    parent: str = ""  # name of the feature (for functions, permissions, rules ...) or actor (for permissions)
    phase: str = ""  # release phase or priority as the source says it
    source_ref: str = ""  # the id the source itself uses, such as "BR-01" or "F03"
    aliases: list[str] = []  # other names the sources use for the same thing (actors and terms mostly)
    status: str = "proposed"  # proposed / confirmed / changed / out_of_scope
    sources: list[Citation] = Field(min_length=1)

    @field_validator("type")
    @classmethod
    def _type(cls, value: str) -> str:
        value = value.strip().lower()
        if value not in ITEM_TYPES:
            raise ValueError(f"type must be one of {', '.join(ITEM_TYPES)}")
        return value


class Gap(Contract):
    kind: str
    question: str
    why: str = ""
    items: list[str] = []  # names (chunk pass) or REQ ids (consolidation) the question is about
    sources: list[Citation] = []
    suggestion: str = ""
    blocking: bool = False

    @field_validator("kind")
    @classmethod
    def _kind(cls, value: str) -> str:
        value = value.strip().lower()
        return value if value in QUESTION_KINDS else "scope"


class ChunkResult(Contract):
    """What the Business Analyst finds in one chunk of one source."""
    items: list[Item] = []
    gaps: list[Gap] = []
    read: list[str] = []  # locations it read, to show coverage


class Merge(Contract):
    keep: str  # REQ id
    drop: list[str] = Field(min_length=1)  # REQ ids that are the same thing as `keep`
    name: str = ""  # better name for the kept item, if any


class Consolidation(Contract):
    """The Business Analyst's pass over the whole merged inventory."""
    merges: list[Merge] = []
    gaps: list[Gap] = []


class Inventory(Contract):
    items: list[Item] = []
    gaps: list[Gap] = []
    coverage: dict[str, list[str]] = {}  # source id -> locations read


def _key(item: Item) -> tuple[str, str, str]:
    norm = lambda s: re.sub(r"\W+", " ", s.lower()).strip()
    return item.type, norm(item.parent), norm(item.name)


def merge_chunks(chunks: list[tuple[str, ChunkResult]]) -> Inventory:
    """Items of every chunk in reading order; the same type, parent and name across chunks is one item whose
    citations and aliases are joined. Ids are assigned after merging, so they have no gaps."""
    merged: dict[tuple, Item] = {}
    gaps: list[Gap] = []
    coverage: dict[str, list[str]] = {}
    for source_id, chunk in chunks:
        coverage.setdefault(source_id, [])
        coverage[source_id] += [r for r in chunk.read if r not in coverage[source_id]]
        for item in chunk.items:
            key = _key(item)
            if key in merged:
                current = merged[key]
                citations = current.sources + [c for c in item.sources if c not in current.sources]
                aliases = current.aliases + [a for a in item.aliases if a not in current.aliases]
                text = current.text if len(current.text) >= len(item.text) else item.text
                merged[key] = current.model_copy(update={"sources": citations, "aliases": aliases, "text": text})
            else:
                merged[key] = item
        gaps += chunk.gaps
    items = [item.model_copy(update={"id": f"REQ-{index:03d}"}) for index, item in enumerate(merged.values(), start=1)]
    return Inventory(items=items, gaps=_unique_gaps(gaps), coverage=coverage)


def apply_consolidation(inventory: Inventory, consolidation: Consolidation) -> Inventory:
    """Merge the items the Business Analyst found to be the same; renumber; questions point at the new ids."""
    by_id = {item.id: item for item in inventory.items}
    dropped: dict[str, str] = {}
    for merge in consolidation.merges:
        if merge.keep not in by_id or merge.keep in dropped:
            continue
        keep = by_id[merge.keep]
        for other_id in merge.drop:
            other = by_id.get(other_id)
            if other is None or other_id == merge.keep or other_id in dropped or other.type != keep.type:
                continue  # never merge across types; an unknown id is ignored
            aliases = keep.aliases + [a for a in [other.name, *other.aliases] if a not in keep.aliases and a != keep.name]
            citations = keep.sources + [c for c in other.sources if c not in keep.sources]
            keep = keep.model_copy(update={"aliases": aliases, "sources": citations})
            dropped[other_id] = merge.keep
        if merge.name and merge.name != keep.name:
            keep = keep.model_copy(update={"name": merge.name, "aliases": [keep.name, *keep.aliases]})
        by_id[merge.keep] = keep
    survivors = [by_id[item.id] for item in inventory.items if item.id not in dropped]
    renumber = {item.id: f"REQ-{index:03d}" for index, item in enumerate(survivors, start=1)}
    renumber.update({old: renumber[new] for old, new in dropped.items() if new in renumber})
    items = [item.model_copy(update={"id": renumber[item.id]}) for item in survivors]
    gaps = []
    for gap in [*inventory.gaps, *consolidation.gaps]:
        gaps.append(gap.model_copy(update={"items": [renumber.get(i, i) for i in gap.items]}))
    return Inventory(items=items, gaps=_unique_gaps(gaps), coverage=inventory.coverage)


def _unique_gaps(gaps: list[Gap]) -> list[Gap]:
    seen, out = set(), []
    for gap in gaps:
        key = re.sub(r"\W+", " ", gap.question.lower()).strip()
        if key and key not in seen:
            seen.add(key)
            out.append(gap)
    return out


def check(inventory: Inventory, source_ids: set[str]) -> list[str]:
    """Problems a reviewer must see: items without a known source, duplicate ids, permissions of unknown actors."""
    problems = []
    ids = [item.id for item in inventory.items]
    if len(ids) != len(set(ids)):
        problems.append("item ids repeat")
    for item in inventory.items:
        unknown = [c.source for c in item.sources if c.source not in source_ids]
        if unknown:
            problems.append(f"{item.id} cites unknown sources {', '.join(sorted(set(unknown)))}")
    actors = {n.lower() for item in inventory.items if item.type == "actor" for n in [item.name, *item.aliases]}
    for item in inventory.items:
        if item.type == "permission" and item.parent and item.parent.lower() not in actors:
            problems.append(f"{item.id} gives a permission to {item.parent}, who is not listed as an actor")
    return problems


TYPE_TITLES = {"goal": "Goals", "actor": "Actors", "permission": "Permissions", "feature": "Features",
               "function": "Functions", "flow": "Flows", "rule": "Business rules", "data": "Data",
               "integration": "Integrations", "exception": "Exceptions", "acceptance": "Acceptance criteria",
               "nfr": "Non-functional requirements", "constraint": "Constraints", "option": "Deployment options",
               "term": "Glossary"}


def render(title: str, inventory: Inventory, source_names: dict[str, str]) -> str:
    cell = lambda s: " ".join(str(s).split()).replace("|", "\\|") or "—"
    cite = lambda item: "; ".join(f"{source_names.get(c.source, c.source)} {c.location}" for c in item.sources)
    lines = [f"# Requirement inventory: {title}", "",
             f"{len(inventory.items)} items from {len(inventory.coverage)} source(s); {len(inventory.gaps)} open question(s).", ""]
    for kind in ITEM_TYPES:
        items = [i for i in inventory.items if i.type == kind]
        if not items:
            continue
        lines += [f"## {TYPE_TITLES[kind]}", "", "| ID | Name | Requirement | Of | Phase | Source ref | Sources |",
                  "|---|---|---|---|---|---|---|"]
        for item in items:
            name = item.name + (f" (also: {', '.join(item.aliases)})" if item.aliases else "")
            lines.append(f"| {item.id} | {cell(name)} | {cell(item.text)} | {cell(item.parent)} | {cell(item.phase)} | "
                         f"{cell(item.source_ref)} | {cell(cite(item))} |")
        lines.append("")
    if inventory.gaps:
        lines += ["## Open questions", "", "| Kind | Question | About | Blocking |", "|---|---|---|---|"]
        for gap in inventory.gaps:
            lines.append(f"| {gap.kind} | {cell(gap.question)} | {cell(', '.join(gap.items))} | {'yes' if gap.blocking else 'no'} |")
        lines.append("")
    lines += ["## Coverage", "", "| Source | Read |", "|---|---|"]
    for source_id, read in inventory.coverage.items():
        lines.append(f"| {cell(source_names.get(source_id, source_id))} | {cell(', '.join(read))} |")
    return "\n".join(lines).rstrip() + "\n"
