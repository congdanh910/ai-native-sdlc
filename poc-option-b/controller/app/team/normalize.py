"""Repair small, common deviations in model output before strict validation.

Only shape is repaired (a list where a string is expected, missing ids). Content is
never invented; anything else fails validation and is sent back to the model.
"""

import re

LINE_PREFIX = re.compile(r"^\s*(?:[-*]|\d+[.)])\s*")
BLOCKING = re.compile(r"^\[blocking\]\s*[-*]?\s*", re.I)


def bullet_lines(value) -> str:
    if isinstance(value, str):
        return value
    if not isinstance(value, list):
        return "" if value is None else str(value)
    lines = []
    for item in value:
        text = str(item).strip()
        blocking = bool(BLOCKING.match(text))
        text = LINE_PREFIX.sub("", BLOCKING.sub("", text)).strip()
        if text:
            lines.append(f"- {'[blocking] ' if blocking else ''}{text}")
    return "\n".join(lines)


def intent(data: dict, title: str) -> dict:
    data = dict(data)
    data["title"] = title
    for key in ("outcome", "open_questions"):
        data[key] = bullet_lines(data.get(key, ""))
    for key in ("problem", "users_and_systems", "constraints"):
        if isinstance(data.get(key), list):
            data[key] = " ".join(str(item) for item in data[key])
        data.setdefault(key, "")
    return data


def _criteria(items, prefix: str) -> list:
    """Ids are always renumbered AC-01, AC-02 (or EC-..) in order; Given / When / Then lists become lines."""
    criteria = []
    for index, item in enumerate(items or [], start=1):
        if isinstance(item, str):
            item = {"text": item}
        if isinstance(item, dict):
            item = dict(item)
            item.pop("id", None)
            if "name" in item and "text" not in item:
                item["text"] = item.pop("name")
            item.pop("name", None)
            for key in ("given", "when", "then"):
                value = item.get(key)
                if isinstance(value, list):
                    item[key] = "\n".join(str(v) for v in value if v)
                elif value is None:
                    item[key] = ""
            item = {"id": f"{prefix}-{index:02d}", **item}
        criteria.append(item)
    return criteria


def spec(data: dict) -> dict:
    data = dict(data)
    data["acceptance_criteria"] = _criteria(data.get("acceptance_criteria"), "AC")
    data["edge_cases"] = _criteria(data.get("edge_cases"), "EC")
    data["self_check"] = self_check(data.get("self_check"))
    data["questions"] = [{"question": q, "blocking": False} if isinstance(q, str) else q
                         for q in data.get("questions") or []]
    for key in ("user_stories", "assumptions", "concerns"):
        if isinstance(data.get(key), str):
            data[key] = [data[key]] if data[key] else []
    return data


def plan(data: dict, validations: list[str]) -> dict:
    data = dict(data)
    tasks = []
    for task in data.get("tasks") or []:
        if not isinstance(task, dict):
            tasks.append(task)
            continue
        task = dict(task)
        for key in ("depends_on", "modify", "create", "provides", "references", "acceptance_criteria", "validation"):
            if task.get(key) is None:
                task[key] = []
            elif isinstance(task[key], str):
                task[key] = [task[key]]
        if not task["validation"]:
            task["validation"] = list(validations)
        task["references"] = [{"file": r, "symbol": None} if isinstance(r, str) else r for r in task["references"]]
        tasks.append(task)
    data["tasks"] = tasks
    data.setdefault("summary", "")
    data["design"] = _design(data.get("design"))
    data["self_check"] = self_check(data.get("self_check"))
    return data


DESIGN_LISTS = ("preflight", "findings", "domain_objects", "business_rules", "sequences", "error_flows", "rule_notes",
                "element_ids")


def _design(design) -> dict:
    """Missing or malformed sections become empty lists; a row's missing columns become empty strings."""
    if not isinstance(design, dict):
        return {}
    design = {key: value for key, value in design.items() if key in DESIGN_LISTS}
    for key in DESIGN_LISTS:
        items = design.get(key)
        if not isinstance(items, list):
            design[key] = []
            continue
        rows = []
        for item in items:
            if not isinstance(item, dict):
                continue
            item = {k: ("" if v is None else v if isinstance(v, str) else _sentence(v)) for k, v in item.items()}
            if key in ("preflight", "findings"):
                item.setdefault("finding", item.pop("text", ""))
            elif key == "sequences":
                item.setdefault("title", "Main flow")
                item.setdefault("mermaid", "")
            else:
                item.setdefault("name", "")
                item.setdefault("detail", "")
            rows.append(item)
        design[key] = rows
    return design


def test_plan(data: dict) -> dict:
    data = dict(data)
    cases = []
    for index, case in enumerate(data.get("cases") or [], start=1):
        if isinstance(case, dict):
            case = {"id": f"TC-{index}", "expected_pass": False, **case}
            for key in ("description", "expected"):
                case[key] = _sentence(case.get(key))
            if case.get("expected_pass") is None:
                case["expected_pass"] = False
            kind = str(case.get("type") or "unit").lower().replace("end-to-end", "e2e")
            case["type"] = kind if kind in ("unit", "integration", "e2e") else "unit"
            severity = str(case.get("severity") or "Major").capitalize()
            case["severity"] = severity if severity in ("Critical", "Major", "Minor") else "Major"
        cases.append(case)
    data["cases"] = cases
    data["self_check"] = self_check(data.get("self_check"))
    return data


def review(data: dict) -> dict:
    data = dict(data)
    findings = []
    for item in data.get("findings") or []:
        if isinstance(item, str):
            item = {"severity": "nit", "text": item}
        elif isinstance(item, dict) and isinstance(item.get("severity"), str):
            item = {**item, "severity": item["severity"].lower()}
        findings.append(item)
    data["findings"] = findings
    data["coverage"] = {k: v if isinstance(v, list) else [v] for k, v in (data.get("coverage") or {}).items()}
    if data.get("verdict") not in ("pass", "concerns"):
        data["verdict"] = "concerns" if any(f.get("severity") == "important" for f in findings if isinstance(f, dict)) else "pass"
    data.pop("markdown", None)
    return data


def _sentence(value):
    """A list or mapping where one sentence is expected: join its parts instead of failing."""
    if isinstance(value, list):
        return "; ".join(_sentence(item) for item in value if item not in (None, ""))
    if isinstance(value, dict):
        return "; ".join(f"{key}: {_sentence(item)}" for key, item in value.items())
    return value if value is None or isinstance(value, str) else str(value)


def srs(data: dict) -> dict:
    """Lists the model wrote as one string become lists; a bare question string becomes non-blocking."""
    data = dict(data)

    def as_list(value):
        if value is None:
            return []
        if isinstance(value, str):
            return [line.strip().lstrip("-*0123456789. ").strip() for line in value.splitlines() if line.strip()]
        return value

    for key in ("assumptions", "out_of_scope", "concerns", "definitions", "roles", "entities", "relationships",
                "business_rules", "states", "features", "qualities", "experience", "flows", "dependencies"):
        data[key] = as_list(data.get(key))
    data["questions"] = [{"question": q, "blocking": False} if isinstance(q, str) else q
                         for q in as_list(data.get("questions"))]
    for key in SRS_ROWS:
        data[key] = _flatten_rows(data[key])
    for feature in data["features"]:
        if isinstance(feature, dict):
            feature["requirements"] = _flatten_rows(as_list(feature.get("requirements")))
            for story in as_list(feature.get("stories")):
                if isinstance(story, dict) and isinstance(story.get("actors"), str):
                    story["actors"] = [a.strip() for a in story["actors"].split(",") if a.strip()]
            feature["stories"] = as_list(feature.get("stories"))
    for flow in data["flows"]:
        if isinstance(flow, dict):
            for key in ("steps", "alternatives", "negatives"):
                flow[key] = as_list(flow.get(key))
    data["trace_notes"] = [n for n in as_list(data.get("trace_notes")) if isinstance(n, dict)]
    _refs(data)
    return data


def _refs(value) -> None:
    """refs written as "REQ-001, REQ-004" become a list of ids, at any depth."""
    if isinstance(value, dict):
        if "refs" in value:
            refs = value["refs"]
            if isinstance(refs, str):
                refs = re.findall(r"REQ-\d+", refs)
            value["refs"] = [str(r).strip() for r in refs or [] if str(r).strip()]
        for child in value.values():
            _refs(child)
    elif isinstance(value, list):
        for child in value:
            _refs(child)


def sds(data: dict) -> dict:
    data = dict(data)

    def as_list(value):
        if value is None:
            return []
        if isinstance(value, str):
            return [line.strip().lstrip("-*0123456789. ").strip() for line in value.splitlines() if line.strip()]
        return value

    for key in ("assumptions", "traceability", "objects", "ui_principles", "screens", "components", "stack", "modules",
                "tables", "process", "deployment", "principles", "api_standards", "endpoints", "errors",
                "authorization", "threats", "data_protection", "secrets", "nfr", "decisions", "metrics", "future",
                "conventions", "integrations", "concerns"):
        data[key] = as_list(data.get(key))
    data["questions"] = [{"question": q, "blocking": False} if isinstance(q, str) else q
                         for q in as_list(data.get("questions"))]
    for key in SDS_ROWS:
        data[key] = _flatten_rows(data[key])
    if isinstance(data.get("purposes"), list):  # [{"id": ..., "purpose": ...}] -> {id: purpose}
        data["purposes"] = {p.get("id") or p.get("story"): p.get("purpose") for p in data["purposes"] if isinstance(p, dict)}
    for endpoint in data["endpoints"]:
        if isinstance(endpoint, dict) and isinstance(endpoint.get("stories"), str):
            endpoint["stories"] = [s.strip() for s in endpoint["stories"].split(",") if s.strip()]
    for item in data["errors"]:
        if isinstance(item, dict) and isinstance(item.get("http"), str) and item["http"].strip().isdigit():
            item["http"] = int(item["http"].strip())
    for scenario in data["process"]:
        if isinstance(scenario, dict):
            scenario["steps"] = as_list(scenario.get("steps"))
    for decision in data["decisions"]:
        if isinstance(decision, dict):
            decision["options"] = as_list(decision.get("options"))
    return data


STATUS_WORDS = {"pass": "pass", "passed": "pass", "ok": "pass", "yes": "pass", "true": "pass", "satisfied": "pass",
                "deferred": "deferred", "defer": "deferred", "fail": "fail", "failed": "fail", "no": "fail",
                "false": "fail", "n/a": "n/a", "na": "n/a", "not applicable": "n/a"}


def self_check(value) -> list:
    """[{id, status, note}] from a list, or from {id: status} / {id: {status, note}}; unknown words become fail."""
    if isinstance(value, dict):
        value = [{"id": k, **(v if isinstance(v, dict) else {"status": v})} for k, v in value.items()]
    out = []
    for item in value if isinstance(value, list) else []:
        if not isinstance(item, dict) or not item.get("id"):
            continue
        status = STATUS_WORDS.get(str(item.get("status", "")).strip().lower(), "fail")
        out.append({"id": str(item["id"]), "status": status, "note": _sentence(item.get("note")) or ""})
    return out


# Fields that hold text in the SRS and SDS contracts; a model often answers them with a list.
SRS_ROWS = ("definitions", "roles", "entities", "relationships", "business_rules", "qualities", "experience",
            "dependencies")
SDS_ROWS = ("traceability", "objects", "screens", "components", "stack", "modules", "tables", "deployment",
            "errors", "threats", "authorization", "nfr", "metrics", "conventions", "integrations")
KEEP_LISTS = {"actors", "steps", "alternatives", "negatives", "stories", "requirements", "states", "changes",
              "options", "refs"}


def _flatten_rows(rows) -> list:
    out = []
    for row in rows if isinstance(rows, list) else []:
        if isinstance(row, dict):
            row = {k: (", ".join(str(x) for x in v if x not in (None, "")) if isinstance(v, list) and k not in KEEP_LISTS
                       else _sentence(v) if isinstance(v, dict) and k not in KEEP_LISTS else v) for k, v in row.items()}
        out.append(row)
    return out
