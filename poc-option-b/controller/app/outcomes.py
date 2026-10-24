"""Desired outcomes to suggest for a feature request, from what the repository already says about its user story.

The deterministic suggestions quote their source (an SRS requirement or business rule, an SDS API, an SDS error); the
person edits them before submitting, and the spec gate still checks the criteria the Business Analyst writes.
"""

import re

GENERIC_ERRORS = {"AUTHENTICATION_REQUIRED", "VALIDATION_ERROR", "INTERNAL_ERROR"}


def _rows(text: str) -> list[list[str]]:
    return [[c.strip() for c in line.strip().strip("|").split("|")] for line in text.splitlines()
            if line.lstrip().startswith("|") and not re.match(r"^\s*\|[\s:|-]+\|\s*$", line)]


def _shall(text: str) -> str:
    """'The system shall register X.' -> 'The system registers X' is not attempted; the requirement is kept, without
    its 'The system shall' prefix, as a capability the story delivers."""
    text = re.sub(r"^(the system|the application|the platform)\s+shall\s+", "", text.strip(), flags=re.IGNORECASE)
    return (text[:1].upper() + text[1:]).rstrip(".")


def story_context(srs: str, sds: str, story: str) -> dict:
    """What the SRS and the SDS say about one story: its statement, requirements and rules, APIs and their errors."""
    prefix = story.split("-")[0]
    statement = ""
    match = re.search(rf"^####\s+[\d.]+\s+{re.escape(story)}:.*?$(.*?)(?=^#### |\Z)", srs, re.MULTILINE | re.DOTALL)
    if match:
        statement = " ".join(line.strip() for line in match.group(1).splitlines() if line.strip()).replace("**", "")
    # requirements traced to the story, else every requirement of its feature
    frs = {r[0]: r for r in _rows(srs) if len(r) >= 3 and re.fullmatch(rf"{prefix}-FR-\d+", r[0])}
    # traceability rows (the baseline appendix) that name the story: the requirements named with it most often are its
    # own; a data or feature row names every requirement of the feature once
    naming = [" ".join(row) for row in _rows(srs) if story in " ".join(row)]
    counts: dict[str, int] = {}
    for row in naming:
        for fr in set(re.findall(rf"{prefix}-FR-\d+", row)):
            counts[fr] = counts.get(fr, 0) + 1
    traced = {fr for fr, n in counts.items() if n == max(counts.values())} if counts else set()
    chosen = [frs[f] for f in sorted(traced) if f in frs] or list(frs.values())
    apis = [r for r in _rows(sds) if len(r) >= 3 and re.fullmatch(r"`?API-[A-Z]+-\d+`?", r[0]) and story in r[1]]
    errors: dict[str, str] = {}
    for api in apis:
        api_id = api[0].strip("`")
        section = re.search(rf"^####\s+{re.escape(api_id)}\b.*?$(.*?)(?=^#### |\Z)", sds, re.MULTILINE | re.DOTALL)
        for code in re.findall(r"`([A-Z_]+)`", re.search(r"Errors:(.*)", section.group(1)).group(1)) \
                if section and re.search(r"Errors:(.*)", section.group(1)) else []:
            errors.setdefault(code, "")
    for row in _rows(sds):
        if len(row) >= 3 and row[1].strip("`") in errors:
            errors[row[1].strip("`")] = row[2]
    return {"story": story, "statement": statement,
            "requirements": [{"id": r[0], "function": r[1], "text": r[2], "rule": r[3] if len(r) > 3 else ""} for r in chosen],
            "apis": [{"id": a[0].strip("`"), "purpose": a[2]} for a in apis],
            "errors": [{"code": code, "meaning": meaning} for code, meaning in errors.items()]}


def suggest(context: dict) -> list[dict]:
    """Outcome lines with their source, in the order a reader checks them: capabilities, rules, edge cases."""
    lines: list[dict] = []

    def add(text: str, source: str) -> None:
        text = " ".join(text.split()).rstrip(".")
        if len(re.sub(r"[\W_]", "", text)) < 3 or text.lower() in ("none", "n/a", "not applicable"):
            return  # an empty table cell: "—", "N/A"
        if all(text.lower() != line["text"].lower() for line in lines):
            lines.append({"text": text, "source": source})
    for api in context["apis"]:
        add(api["purpose"], api["id"])
    for fr in context["requirements"]:
        add(_shall(fr["text"]), fr["id"])
    for fr in context["requirements"]:
        if fr["rule"]:
            add(fr["rule"], f"{fr['id']} rule")
    for error in context["errors"]:
        if error["code"] not in GENERIC_ERRORS and error["meaning"]:
            add(f"When {error['meaning'][:1].lower()}{error['meaning'][1:].rstrip('.')}, the request is refused with "
                f"{error['code']}", error["code"])
    return lines[:12]
