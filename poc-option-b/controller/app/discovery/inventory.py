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
    status: str = "proposed"  # proposed / confirmed / changed / out_of_scope, changed only by decisions
    sources: list[Citation] = Field(min_length=1)

    @field_validator("type")
    @classmethod
    def _type(cls, value: str) -> str:
        value = value.strip().lower()
        if value not in ITEM_TYPES:
            raise ValueError(f"type must be one of {', '.join(ITEM_TYPES)}")
        return value


STAGES = ("baseline", "later")


class Gap(Contract):
    kind: str
    question: str
    why: str = ""
    items: list[str] = []  # names (chunk pass) or REQ ids (consolidation) the question is about
    sources: list[Citation] = []
    suggestion: str = ""
    blocking: bool = False
    # baseline: needed to fix scope, actors, main flows, customer rules, integrations, compliance.
    # later: detail the SRS, SDS or a story's spec settles; the controller defers it with that reason.
    stage: str = "baseline"

    @field_validator("stage")
    @classmethod
    def _stage(cls, value: str) -> str:
        value = (value or "").strip().lower()
        return value if value in STAGES else "baseline"

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


class MinutesDecision(Contract):
    text: str = Field(min_length=1)
    question: str = ""  # Q-003 when it answers an open question
    items: list[str] = []  # REQ ids it is about
    location: str = ""


class MinutesResult(Contract):
    """What the Business Analyst reads in one part of meeting minutes: the decisions taken there."""
    decisions: list[MinutesDecision] = []
    read: list[str] = []


class Merge(Contract):
    keep: str  # REQ id
    drop: list[str] = Field(min_length=1)  # REQ ids that are the same thing as `keep`
    name: str = ""  # better name for the kept item, if any


STATUSES = ("proposed", "confirmed", "changed", "out_of_scope")


class Update(Contract):
    """What a decision changes in one item: its status, its text, or both."""
    id: str
    decision: str  # D-001
    status: str = ""
    text: str = ""

    @field_validator("status")
    @classmethod
    def _status(cls, value: str) -> str:
        value = value.strip().lower().replace(" ", "_")
        if value and value not in STATUSES:
            raise ValueError(f"status must be one of {', '.join(STATUSES)}")
        return value


class Link(Contract):
    """An item read without its parent (a function on a flow page, say) and the feature or actor it belongs to."""
    id: str
    parent: str = Field(min_length=1)


class SuggestedAnswer(Contract):
    question: str  # Q-023
    suggestion: str


class Consolidation(Contract):
    """The Business Analyst's pass over the whole merged inventory."""
    suggestions: list[SuggestedAnswer] = []  # answers it suggests for open questions asked in earlier rounds
    merges: list[Merge] = []
    gaps: list[Gap] = []
    updates: list[Update] = []  # what the accepted decisions change
    links: list[Link] = []  # parents of items that have none


class Inventory(Contract):
    items: list[Item] = []
    gaps: list[Gap] = []
    coverage: dict[str, list[str]] = {}  # source id -> locations read


def _norm(text: str) -> str:
    return re.sub(r"\W+", " ", text.lower()).strip()


def _key(item: Item) -> tuple[str, str, str]:
    return item.type, _norm(item.parent), _norm(item.name)


def _number(item_id: str) -> int:
    match = re.fullmatch(r"REQ-(\d+)", item_id)
    return int(match[1]) if match else 0


def merge_chunks(chunks: list[tuple[str, ChunkResult]], previous: Inventory | None = None) -> Inventory:
    """Items of every chunk in reading order; the same type, parent and name across chunks is one item whose
    citations and aliases are joined. An item the previous round already had (by name or alias) keeps its id and
    status, so answers and decisions that cite REQ ids stay valid; new items are numbered after the highest id."""
    merged: dict[tuple, Item] = {}
    gaps: list[Gap] = []
    coverage: dict[str, list[str]] = {}
    for source_id, chunk in chunks:
        coverage.setdefault(source_id, [])
        coverage[source_id] += [r for r in chunk.read if r not in coverage[source_id]]
        for item in chunk.items:
            key = _key(item)
            merged[key] = _join(merged[key], item) if key in merged else item
        gaps += chunk.gaps
    known: dict[tuple, Item] = {}
    for old in (previous.items if previous else []):
        for name in [old.name, *old.aliases]:
            known.setdefault((old.type, _norm(old.parent), _norm(name)), old)
    next_number = max([_number(i.id) for i in (previous.items if previous else [])] + [0]) + 1
    by_id: dict[str, Item] = {}
    for item in merged.values():
        old = next((known[k] for k in [_key(item), *((item.type, _norm(item.parent), _norm(a)) for a in item.aliases)]
                    if k in known), None)
        if old is None:
            item_id, next_number = f"REQ-{next_number:03d}", next_number + 1
            by_id[item_id] = item.model_copy(update={"id": item_id})
        elif old.id in by_id:
            by_id[old.id] = _join(by_id[old.id], item)
        else:
            aliases = [a for a in old.aliases if a not in item.aliases and a != item.name]
            by_id[old.id] = item.model_copy(update={"id": old.id, "name": old.name, "status": old.status,
                                                    "aliases": [*item.aliases, *aliases] if old.name == item.name
                                                    else [item.name, *item.aliases, *aliases]})
    items = sorted(by_id.values(), key=lambda i: _number(i.id))
    return Inventory(items=items, gaps=_unique_gaps(gaps), coverage=coverage)


def _join(current: Item, item: Item) -> Item:
    citations = current.sources + [c for c in item.sources if c not in current.sources]
    names = [item.name] if _norm(item.name) != _norm(current.name) else []
    aliases = current.aliases + [a for a in [*names, *item.aliases] if a not in current.aliases]
    text = current.text if len(current.text) >= len(item.text) else item.text
    return current.model_copy(update={"sources": citations, "aliases": aliases, "text": text})


def apply_consolidation(inventory: Inventory, consolidation: Consolidation, decisions: set[str] = frozenset()) -> Inventory:
    """Merge the items the Business Analyst found to be the same (ids never change; a dropped id points at the kept
    one in questions), then apply what accepted decisions change. An update naming an unknown decision is ignored."""
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
            keep = _join(keep, other)
            dropped[other_id] = merge.keep
        if merge.name and merge.name != keep.name:
            keep = keep.model_copy(update={"name": merge.name, "aliases": [keep.name, *keep.aliases]})
        by_id[merge.keep] = keep
    for link in consolidation.links:
        target = dropped.get(link.id, link.id)
        if target in by_id and not by_id[target].parent:
            by_id[target] = by_id[target].model_copy(update={"parent": link.parent.strip()})
    for update in consolidation.updates:
        target = dropped.get(update.id, update.id)
        if target not in by_id or update.decision not in decisions:
            continue
        item = by_id[target]
        cite = Citation(source=f"decision:{update.decision}", location="")
        by_id[target] = item.model_copy(update={"status": update.status or item.status, "text": update.text or item.text,
                                                "sources": item.sources + ([cite] if cite not in item.sources else [])})
    _cascade_out_of_scope(by_id, {dropped.get(u.id, u.id) for u in consolidation.updates if u.decision in decisions})
    items = [by_id[item.id] for item in inventory.items if item.id not in dropped]
    gaps = [gap.model_copy(update={"items": [dropped.get(i, i) for i in gap.items]})
            for gap in [*inventory.gaps, *consolidation.gaps]]
    return Inventory(items=items, gaps=_unique_gaps(gaps), coverage=inventory.coverage)


def _cascade_out_of_scope(by_id: dict[str, Item], decided: set[str]) -> None:
    """An item out of scope takes the items that belong to it (functions of a feature, permissions of an actor ...)
    out with it, citing the same decision; an item a decision of this pass names keeps what that decision says."""
    changed = True
    while changed:
        changed = False
        for parent in [i for i in by_id.values() if i.status == "out_of_scope"]:
            names = {_norm(n) for n in [parent.name, *parent.aliases]}
            cites = [c for c in parent.sources if c.source.startswith("decision:")][-1:]
            for item_id, item in by_id.items():
                if item.status != "out_of_scope" and item_id not in decided and _norm(item.parent) in names:
                    by_id[item_id] = item.model_copy(update={
                        "status": "out_of_scope", "sources": item.sources + [c for c in cites if c not in item.sources]})
                    changed = True


def _unique_gaps(gaps: list[Gap]) -> list[Gap]:
    seen, out = set(), []
    for gap in gaps:
        key = re.sub(r"\W+", " ", gap.question.lower()).strip()
        if key and key not in seen:
            seen.add(key)
            out.append(gap)
    return out


def check(inventory: Inventory, source_ids: set[str], decisions: set[str] = frozenset()) -> list[str]:
    """Problems a reviewer must see: items without a known source or decision, duplicate ids, permissions of
    unknown actors."""
    problems = []
    ids = [item.id for item in inventory.items]
    if len(ids) != len(set(ids)):
        problems.append("item ids repeat")
    for item in inventory.items:
        unknown = [c.source for c in item.sources
                   if c.source not in source_ids and c.source.removeprefix("decision:") not in decisions]
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
    cite = lambda item: "; ".join(c.source.removeprefix("decision:") if c.source.startswith("decision:")
                                  else f"{source_names.get(c.source, c.source)} {c.location}" for c in item.sources)
    lines = [f"# Requirement inventory: {title}", "",
             f"{len(inventory.items)} items from {len(inventory.coverage)} source(s); {len(inventory.gaps)} open question(s).", ""]
    for kind in ITEM_TYPES:
        items = [i for i in inventory.items if i.type == kind]
        if not items:
            continue
        lines += [f"## {TYPE_TITLES[kind]}", "", "| ID | Name | Requirement | Of | Phase | Status | Source ref | Sources |",
                  "|---|---|---|---|---|---|---|---|"]
        for item in items:
            name = item.name + (f" (also: {', '.join(item.aliases)})" if item.aliases else "")
            lines.append(f"| {item.id} | {cell(name)} | {cell(item.text)} | {cell(item.parent)} | {cell(item.phase)} | {item.status} | "
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
