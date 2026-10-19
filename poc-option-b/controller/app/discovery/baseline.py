"""The baseline: the requirement inventory and decisions the Product Owner signs off for a phase.

The controller checks it at the gate, then commits requirements/ to the run's branch: the inventory, questions,
decisions, the baseline itself (Markdown for people, JSON for the SRS run) and, unless discovery.commit_sources is
false, the raw sources with their hashes. Merging the draft PR completes the discovery; the SRS run reads
requirements/baseline.json from the default branch.
"""

from pathlib import Path
import re

from controller.app.discovery.inventory import Inventory

BASELINE_DIR = "requirements"
BASELINE_JSON = f"{BASELINE_DIR}/baseline.json"
IN_SCOPE = ("proposed", "confirmed", "changed")


def _cited(item_id: str, questions: list[dict]) -> list[dict]:
    return [q for q in questions if item_id in (q.get("items") or [])]


def check(inventory: Inventory, problems: list[str], questions: list[dict], decisions: list[dict],
          applied: list[str]) -> list[dict]:
    """Controller items of the baseline gate; each blocks the approval while it fails."""
    def result(item_id: str, text: str, found: list[str]) -> dict:
        return {"id": item_id, "text": text, "ok": not found, "detail": "; ".join(found[:6])}

    open_ = [q for q in questions if q["status"] == "open"]
    in_scope = [i for i in inventory.items if i.status in IN_SCOPE]
    features = [i for i in in_scope if i.type == "feature"]
    accepted = [d["id"] for d in decisions if d["status"] == "accepted"]
    feature_names = {n.lower() for f in features for n in [f.name, *f.aliases]}
    excluded = {n.lower(): f for f in inventory.items if f.type == "feature" and f.status == "out_of_scope"
                for n in [f.name, *f.aliases]}
    functions = {i.parent.lower() for i in in_scope if i.type == "function"}
    return [
        result("blocking", "No blocking question is open", [f"{q['number']} {q['question'][:80]}" for q in open_ if q["blocking"]]),
        result("conflicts", "Every conflict between sources is resolved",
               [f"{q['number']} {q['question'][:80]}" for q in open_ if q["kind"] == "conflict"]),
        result("decisions", "No decision from minutes waits for its owner",
               [d["id"] for d in decisions if d["status"] == "proposed"]),
        result("applied", "The inventory includes every accepted decision (extract again after new decisions)",
               [d for d in accepted if d not in applied]),
        result("inventory", "Every item cites a source or a decision; ids are unique; permissions name known actors",
               problems),
        result("functions", "Every feature in scope has at least one function, and every function belongs to a feature",
               [f"{f.id} {f.name} has no function" for f in features
                if not ({n.lower() for n in [f.name, *f.aliases]} & functions)]
               + [_orphan(i, excluded.get(i.parent.lower())) for i in in_scope if i.type == "function"
                  and i.parent.lower() not in feature_names]),
    ]


def _orphan(item, feature) -> str:
    if feature is None:
        return f"{item.id} {item.name} belongs to no feature"
    decided = [c.source.removeprefix("decision:") for c in feature.sources if c.source.startswith("decision:")]
    by = f" ({decided[-1]})" if decided else ""
    return (f"{item.id} {item.name} belongs to {feature.id} {feature.name}, which is out of scope{by}; "
            "check the inventory again to take it out too")


def advisories(inventory: Inventory, questions: list[dict]) -> list[dict]:
    """The Business Analyst's side of the gate (design section 6): quality targets without a number. Shown, not
    blocking: a qualitative target such as encryption at rest has no number."""
    numberless = []
    for item in inventory.items:
        if item.status in IN_SCOPE and item.type == "nfr" and not re.search(r"\d", item.text):
            settled = [q for q in _cited(item.id, questions) if q["status"] in ("answered", "deferred")]
            if not settled:
                numberless.append(f"{item.id} {item.name}")
    return [{"id": "nfr-targets", "text": "Quality targets have a number, or a question about them is answered or deferred",
             "status": "warning" if numberless else "pass",
             "note": ("no number: " + "; ".join(numberless[:8]) + ". Fine for a qualitative target; ask otherwise.")
             if numberless else ""}]


def snapshot(title: str, scope: str, inventory: Inventory, version: int, questions: list[dict],
             decisions: list[dict], sources: list[dict]) -> dict:
    """What the SRS run builds on: the items in scope, what is out, the accepted decisions, the deferred questions."""
    return {
        "title": title, "scope": scope, "inventory_version": version,
        "items": [i.model_dump() for i in inventory.items if i.status in IN_SCOPE],
        "out_of_scope": [i.model_dump() for i in inventory.items if i.status == "out_of_scope"],
        "decisions": [{k: d[k] for k in ("id", "text", "items", "role", "basis")} for d in decisions
                      if d["status"] == "accepted"],
        "deferred": [{k: q[k] for k in ("number", "kind", "question", "items", "status_reason")} for q in questions
                     if q["status"] == "deferred"],
        "sources": sources,
    }


def render(data: dict, roles: dict[str, str]) -> str:
    cell = lambda s: " ".join(str(s or "").split()).replace("|", "\\|") or "—"
    items = data["items"]
    counts: dict[str, int] = {}
    for item in items:
        counts[item["type"]] = counts.get(item["type"], 0) + 1
    lines = [f"# Requirement baseline: {data['title']}", "",
             f"Built on requirement inventory v{data['inventory_version']}. "
             f"{len(items)} items in scope, {len(data['out_of_scope'])} out of scope, "
             f"{len(data['decisions'])} decisions, {len(data['deferred'])} deferred questions.", "",
             "## Scope of this phase", "", data["scope"].strip() or "_Not stated._", "",
             "## In scope", "", "| Type | Items |", "|---|---|"]
    lines += [f"| {kind} | {n} |" for kind, n in sorted(counts.items())]
    lines += ["", "The full table is in `inventory.md`; `baseline.json` holds these items for the SRS run.", ""]
    if data["out_of_scope"]:
        lines += ["## Out of scope", "", "| ID | Name | Decided by |", "|---|---|---|"]
        for item in data["out_of_scope"]:
            decided = ", ".join(c["source"].removeprefix("decision:") for c in item["sources"]
                                if c["source"].startswith("decision:"))
            lines.append(f"| {item['id']} | {cell(item['name'])} | {cell(decided)} |")
        lines.append("")
    lines += ["## Decisions", "", "| ID | Decision | About | Owner |", "|---|---|---|---|"]
    lines += [f"| {d['id']} | {cell(d['text'])} | {cell(', '.join(d['items']))} | {cell(roles.get(d['role'], d['role']))} |"
              for d in data["decisions"]] or ["| — | No decisions | | |"]
    lines.append("")
    if data["deferred"]:
        lines += ["## Deferred questions", "", "| # | Question | Why deferred |", "|---|---|---|"]
        lines += [f"| {q['number']} | {cell(q['question'])} | {cell(q['status_reason'])} |" for q in data["deferred"]]
        lines.append("")
    lines += ["## Sources", "", "| File | SHA-256 | Pages |", "|---|---|---|"]
    lines += [f"| {cell(s['filename'])} | `{s['sha256']}` | {s['pages'] or '—'} |" for s in data["sources"]]
    return "\n".join(lines).rstrip() + "\n"


def source_files(sources: list, commit: bool) -> dict[str, bytes]:
    """requirements/sources/<file> for each raw source; names keep the file name, prefixed when two collide."""
    if not commit:
        return {}
    files: dict[str, bytes] = {}
    for source in sources:
        name = re.sub(r"[^\w.\- ]+", "_", source.filename, flags=re.UNICODE).strip() or "source"
        path = f"{BASELINE_DIR}/sources/{name}"
        if path in files:
            path = f"{BASELINE_DIR}/sources/{source.sha256[:8]}-{name}"
        files[path] = Path(source.path).read_bytes()
    return files


def sources_md(sources: list, committed: bool) -> str:
    lines = ["# Sources", "", "| File | Purpose | SHA-256 | Pages | Added by |", "|---|---|---|---|---|"]
    lines += [f"| {s.filename} | {s.purpose} | `{s.sha256}` | {s.pages or '—'} | {s.uploaded_by} |" for s in sources]
    lines += ["", "The files are in `sources/`." if committed else
              "The files are not in the repository (discovery.commit_sources is false); the hashes identify them."]
    return "\n".join(lines) + "\n"
