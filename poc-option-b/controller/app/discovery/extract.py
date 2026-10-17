"""Extraction plumbing that needs no model: split sources into parts, repair answers, check what was read.

A part is what one Business Analyst call reads: a range of PDF pages (the CLI reads the file itself), one image,
or a slice of a text view (sent inline, with the bracketed locations of the converter).
"""

from dataclasses import asdict, dataclass
from pathlib import Path
import re

from controller.app.discovery.inventory import ITEM_TYPES

FILE_KINDS = ("pdf", "image")  # read by the CLI from the file; everything else has a text view

TYPE_SYNONYMS = {"entity": "data", "data_entity": "data", "object": "data", "role": "actor", "user": "actor",
                 "stakeholder": "actor", "access": "permission", "right": "permission", "module": "feature",
                 "epic": "feature", "use_case": "function", "usecase": "function", "requirement": "function",
                 "functional": "function", "process": "flow", "workflow": "flow", "business_rule": "rule",
                 "policy": "rule", "interface": "integration", "external_system": "integration", "error": "exception",
                 "edge_case": "exception", "acceptance_criterion": "acceptance", "criteria": "acceptance",
                 "non_functional": "nfr", "nonfunctional": "nfr", "quality": "nfr", "assumption": "constraint",
                 "deployment": "option", "alternative": "option", "glossary": "term", "definition": "term",
                 "objective": "goal", "benefit": "goal"}


@dataclass(frozen=True)
class Part:
    source_id: str
    filename: str
    kind: str
    label: str  # "pages 1-20 of 26", "lines 1-400 of 950"
    file: str = ""  # file name in the sources folder, for a PDF or image the CLI reads
    pages: str = ""  # the Read tool's pages argument, such as "1-20"
    text: str = ""  # the slice of the text view, for every other kind

    def as_input(self) -> dict:
        data = asdict(self)
        return {"source": {"id": data.pop("source_id"), "filename": data.pop("filename"), "kind": data.pop("kind")},
                "part": {"covers": self.label, "pages": self.pages}, "file": self.file, "text": self.text}


def plan_parts(source_id: str, filename: str, kind: str, path: str, pages: int | None, text: str | None, *,
               pages_per_read: int, chars_per_read: int) -> list[Part]:
    """The parts of one source, in reading order."""
    if kind in FILE_KINDS:
        name = Path(path).name
        if kind == "image" or not pages:
            return [Part(source_id, filename, kind, "the whole image" if kind == "image" else "the whole file", file=name,
                         pages="")]
        parts = []
        for start in range(1, pages + 1, pages_per_read):
            end = min(pages, start + pages_per_read - 1)
            parts.append(Part(source_id, filename, kind, f"pages {start}-{end} of {pages}", file=name, pages=f"{start}-{end}"))
        return parts
    lines = (text or "").splitlines()
    parts, start, size = [], 0, 0
    for index, line in enumerate(lines):
        if size and size + len(line) + 1 > chars_per_read:
            parts.append((start, index))
            start, size = index, 0
        size += len(line) + 1
    if start < len(lines):
        parts.append((start, len(lines)))
    return [Part(source_id, filename, kind, f"lines {a + 1}-{b} of {len(lines)}", text="\n".join(lines[a:b]))
            for a, b in parts]


def split(part: Part) -> list[Part]:
    """Two halves of a part, or nothing when it is one page or one line. A dense part can make an answer longer than
    the CLI's output limit; the halves are read again instead of retrying the same size (trial, 2026-10-16)."""
    match = re.fullmatch(r"(pages|lines) (\d+)-(\d+) of (\d+)", part.label)
    if not match or int(match[2]) >= int(match[3]):
        return []
    unit, start, end, total = match[1], int(match[2]), int(match[3]), match[4]
    middle = (start + end) // 2
    halves = []
    for a, b in ((start, middle), (middle + 1, end)):
        label = f"{unit} {a}-{b} of {total}"
        if unit == "pages":
            halves.append(Part(part.source_id, part.filename, part.kind, label, file=part.file, pages=f"{a}-{b}"))
        else:
            lines = part.text.splitlines()[a - start:b - start + 1]
            halves.append(Part(part.source_id, part.filename, part.kind, label, text="\n".join(lines)))
    return halves


def _citation(value, part: Part) -> dict:
    """A chunk reads one source, so every citation names it; a location the model left out is the part's range."""
    location = value.get("location") if isinstance(value, dict) else value
    location = " ".join(str(location or "").split()).strip("[] ")
    return {"source": part.source_id, "location": location or part.label}


def _item_type(value) -> str:
    key = re.sub(r"[\s\-]+", "_", str(value or "").strip().lower())
    return key if key in ITEM_TYPES else TYPE_SYNONYMS.get(key, key)


def _strings(value) -> list[str]:
    if isinstance(value, str):
        value = [value]
    return [" ".join(str(v).split()) for v in value or [] if str(v).strip()]


def repair_chunk(data: dict, part: Part) -> dict:
    items = []
    for raw in data.get("items") or []:
        if not isinstance(raw, dict):
            continue
        name = " ".join(str(raw.get("name") or "").split()) or " ".join(str(raw.get("text") or "").split())[:80]
        citations = raw.get("sources") or raw.get("source") or raw.get("location") or []
        if not isinstance(citations, list):
            citations = [citations]
        seen, cites = set(), []
        for c in (_citation(c, part) for c in citations or [""]):
            if c["location"] not in seen:
                seen.add(c["location"])
                cites.append(c)
        items.append({"type": _item_type(raw.get("type")), "name": name[:200], "text": str(raw.get("text") or "").strip(),
                      "parent": " ".join(str(raw.get("parent") or "").split()),
                      "phase": " ".join(str(raw.get("phase") or "").split()),
                      "source_ref": " ".join(str(raw.get("source_ref") or "").split()),
                      "aliases": [a for a in _strings(raw.get("aliases")) if a != name], "sources": cites})
    return {"items": items, "gaps": [_gap(g, part) for g in data.get("gaps") or [] if isinstance(g, dict)],
            "read": _strings(data.get("read")) or [part.label]}


def _gap(raw: dict, part: Part | None) -> dict:
    sources = [_citation(c, part) for c in raw.get("sources") or []] if part else []
    return {"kind": str(raw.get("kind") or "scope"), "question": " ".join(str(raw.get("question") or "").split()),
            "why": str(raw.get("why") or "").strip(), "items": _strings(raw.get("items")), "sources": sources,
            "suggestion": str(raw.get("suggestion") or "").strip(), "blocking": bool(raw.get("blocking"))}


def repair_consolidation(data: dict) -> dict:
    merges = []
    for raw in data.get("merges") or []:
        if isinstance(raw, dict) and raw.get("keep") and raw.get("drop"):
            merges.append({"keep": str(raw["keep"]).strip(), "drop": _strings(raw["drop"]),
                           "name": " ".join(str(raw.get("name") or "").split())})
    gaps = [_gap(g, None) for g in data.get("gaps") or [] if isinstance(g, dict)]
    links = [{"id": str(l["id"]).strip(), "parent": " ".join(str(l["parent"]).split())}
             for l in data.get("links") or [] if isinstance(l, dict) and l.get("id") and str(l.get("parent") or "").strip()]
    updates = [u for u in data.get("updates") or [] if isinstance(u, dict)]
    suggestions = [{"question": str(x["question"]).strip(), "suggestion": " ".join(str(x["suggestion"]).split())}
                   for x in data.get("suggestions") or []
                   if isinstance(x, dict) and x.get("question") and str(x.get("suggestion") or "").strip()]
    return {"merges": merges, "gaps": [g for g in gaps if g["question"]], "links": links, "updates": updates,
            "suggestions": suggestions}


def compact_items(items: list[dict]) -> list[dict]:
    """What the consolidation pass needs of each item. The text stays whole: a cut text made the Business Analyst
    ask for flow steps the item already listed (trial run, 2026-10-16)."""
    return [{"id": i["id"], "type": i["type"], "name": i["name"], "text": i["text"], "parent": i["parent"],
             "status": i.get("status", "proposed"),
             "sources": [c["location"] for c in i["sources"]]} for i in items]


def repair_minutes(data: dict, part: Part) -> dict:
    out = []
    for raw in data.get("decisions") or []:
        if isinstance(raw, dict) and str(raw.get("text") or "").strip():
            out.append({"text": " ".join(str(raw["text"]).split()), "question": str(raw.get("question") or "").strip(),
                        "items": _strings(raw.get("items")), "location": _citation(raw.get("location") or "", part)["location"]})
    return {"decisions": out, "read": _strings(data.get("read")) or [part.label]}
