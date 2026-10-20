"""Repair small, common deviations in model output before strict validation.

Only shape is repaired (a list where a string is expected, missing ids). Content is
never invented; anything else fails validation and is sent back to the model.
"""

import json
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


def feedback_replies(value) -> list[dict]:
    """[{feedback, change, where}]; a string where becomes a list, a reply without a change is dropped."""
    out = []
    for item in value if isinstance(value, list) else []:
        if not isinstance(item, dict) or not str(item.get("change") or "").strip():
            continue
        where = item.get("where") or []
        where = [w.strip() for w in where.split(",")] if isinstance(where, str) else [str(w) for w in where]
        out.append({"feedback": str(item.get("feedback") or "").strip(), "change": str(item["change"]).strip(),
                    "where": [w for w in where if w]})
    return out


def spec(data: dict) -> dict:
    data = dict(data)
    data["feedback_replies"] = feedback_replies(data.get("feedback_replies"))
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
    data["feedback_replies"] = feedback_replies(data.get("feedback_replies"))

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
    data.pop("refs", None)  # refs belong to the elements, not to the document
    for rel in data.get("relationships", []):
        if isinstance(rel, dict):
            rel.pop("refs", None)
    for states in data.get("states", []):
        if isinstance(states, dict):
            states.pop("refs", None)
            for key in ("states", "changes"):
                for row in states.get(key) or []:
                    if isinstance(row, dict) and key == "changes":
                        row.pop("refs", None)
    _refs(data)
    return data


def options(data: dict) -> dict:
    data = dict(data)
    out = []
    for option in data.get("options") or []:
        if isinstance(option, dict):
            option = dict(option)
            for key in ("pros", "cons"):
                value = option.get(key)
                option[key] = [line.strip("-* ").strip() for line in value.splitlines() if line.strip()] if isinstance(value, str) else value or []
            for key in ("nfr_fit", "cost", "risks", "personal_data", "operations", "summary", "deployment", "integrations"):
                if isinstance(option.get(key), list):
                    option[key] = "; ".join(str(x) for x in option[key])
            stack = option.get("stack") or []
            if isinstance(stack, dict):  # {"backend": "Node.js", ...}
                stack = [{"layer": k, "technology": v} for k, v in stack.items()]
            option["stack"] = [x if isinstance(x, dict) else {"layer": str(x).split(":", 1)[0].strip(),
                                                              "technology": str(x).split(":", 1)[-1].strip()}
                               for x in (stack if isinstance(stack, list) else [stack])]
            option["hosting"] = [_hosting(v) for v in (option.get("hosting") or []) if isinstance(v, dict)]
            option["recommended_hosting"] = str(option.get("recommended_hosting") or "")
            out.append(option)
    data["options"] = out
    data["questions"] = [{"question": q, "blocking": False} if isinstance(q, str) else q for q in data.get("questions") or []]
    _refs(data)
    return data


PLATFORM_WORDS = {"on-premise": "on_premise", "on premise": "on_premise", "on-premises": "on_premise",
                  "on premises": "on_premise", "onprem": "on_premise", "on-prem": "on_premise", "amazon": "aws",
                  "amazon web services": "aws", "microsoft azure": "azure", "google cloud": "gcp",
                  "google cloud platform": "gcp"}


def _hosting(variant: dict) -> dict:
    """One hosting variant: a platform word the project config uses, services as a list, text fields as text."""
    variant = dict(variant)
    platform = str(variant.get("platform") or "").strip().lower()
    variant["platform"] = PLATFORM_WORDS.get(platform, platform.replace(" ", "_").replace("-", "_"))
    services = variant.get("services") or []
    if isinstance(services, dict):  # {"API": "Amazon ECS", ...}
        services = [{"node": k, "service": v} for k, v in services.items()]
    variant["services"] = [s if isinstance(s, dict) else {"node": str(s).split(":", 1)[0].strip(),
                                                          "service": str(s).split(":", 1)[-1].strip()}
                           for s in (services if isinstance(services, list) else [services])]
    for key in ("pros", "cons"):
        value = variant.get(key)
        variant[key] = [line.strip("-* ").strip() for line in value.splitlines() if line.strip()] if isinstance(value, str) else value or []
    for key in ("nfr_fit", "cost", "risks", "personal_data", "operations"):
        if isinstance(variant.get(key), list):
            variant[key] = "; ".join(str(x) for x in variant[key])
    return variant


def onboarding(data: dict) -> dict:
    """Text files the model wrote as lists become text; a bare command string becomes argv; questions as objects."""
    data = dict(data)
    for key in ("agents_md", "review_md", "gitignore", "ci_workflow", "dockerfile", "notes"):
        if isinstance(data.get(key), list):
            data[key] = "\n".join(str(x) for x in data[key])
    if isinstance(data.get("setup"), str):
        data["setup"] = ["sh", "-c", data["setup"]] if data["setup"].strip() else []
    for command in data.get("validation") or []:
        if isinstance(command, dict) and isinstance(command.get("run"), str):
            command["run"] = ["sh", "-c", command["run"]]
    data["questions"] = [{"question": q, "blocking": False} if isinstance(q, str) else q for q in data.get("questions") or []]
    data["feedback_replies"] = feedback_replies(data.get("feedback_replies"))
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
    data["feedback_replies"] = feedback_replies(data.get("feedback_replies"))

    def as_list(value):
        if value is None:
            return []
        if isinstance(value, str):
            return [line.strip().lstrip("-*0123456789. ").strip() for line in value.splitlines() if line.strip()]
        return value

    for key in ("assumptions", "traceability", "objects", "ui_principles", "screens", "components", "stack", "modules",
                "tables", "process", "deployment", "principles", "api_standards", "endpoints", "errors",
                "authorization", "threats", "data_protection", "secrets", "nfr", "decisions", "metrics", "future",
                "conventions", "integrations", "concerns", "zones", "links"):
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
    for key in ("constraints", "storage", "migration", "future", "done"):
        if key in data:
            data[key] = as_list(data.get(key))
    for key in ("definitions", "relationships", "external_systems", "key_scenarios", "observability"):
        if key in data:
            data[key] = _flatten_rows(as_list(data.get(key)))
    for key in ("environments", "resilience"):  # {"production": "..."} or [{"term": ..., "meaning": ...}]
        if key in data:
            value = data[key]
            value = [{"term": k, "meaning": v} for k, v in value.items()] if isinstance(value, dict) else as_list(value)
            data[key] = _flatten_rows([x if isinstance(x, dict) else {"term": str(x), "meaning": ""} for x in value])
    if isinstance(data.get("actors"), str):
        data["actors"] = as_list(data["actors"])
    for item in data.get("states") or []:
        if isinstance(item, dict):
            item["states"] = _flatten_rows([x if isinstance(x, dict) else {"term": str(x), "meaning": ""}
                                            for x in as_list(item.get("states"))])
            item["transitions"] = _flatten_rows(as_list(item.get("transitions")))
    for key in ("tables", "dtos"):
        for row in data.get(key) or []:
            if isinstance(row, dict):
                row["fields"] = [_column(c) for c in as_list(row.get("fields"))]
    for scenario in data["process"]:
        if isinstance(scenario, dict):
            for key in ("participants", "alternatives"):
                scenario[key] = as_list(scenario.get(key))
            scenario["messages"] = [_message(m) for m in as_list(scenario.get("messages")) if _message(m)]
    for endpoint in data["endpoints"]:
        if isinstance(endpoint, dict):
            for key in ("request", "response"):
                if isinstance(endpoint.get(key), (dict, list)):
                    endpoint[key] = json.dumps(endpoint[key], ensure_ascii=False, indent=1)
            endpoint["errors"] = as_list(endpoint.get("errors"))
    for screen in data["screens"]:
        if isinstance(screen, dict):
            screen["layout"] = as_list(screen.get("layout"))
    for component in data["components"]:
        if isinstance(component, dict):
            component["calls"] = as_list(component.get("calls"))
    for key in ("frontend", "backend"):
        tier = data.get(key)
        if isinstance(tier, dict):
            for list_key in ("stack", "responsibilities", "conventions"):
                tier[list_key] = as_list(tier.get(list_key))
            if isinstance(tier.get("structure"), list):
                tier["structure"] = "\n".join(str(x) for x in tier["structure"])
            tier["layers"] = _flatten_rows(as_list(tier.get("layers")))
        elif key in data and not tier:
            data[key] = {}
    auth = data.get("auth")
    if isinstance(auth, dict):
        auth["flow"] = [_message(m) for m in as_list(auth.get("flow")) if _message(m)]
    metrics = []
    for metric in data["metrics"]:
        if isinstance(metric, dict) and "name" not in metric and "term" in metric:
            metric = {"name": metric["term"], "definition": metric.get("meaning", ""), "target": metric.get("target", "")}
        metrics.append(metric)
    data["metrics"] = metrics
    return data


def _column(value) -> dict:
    if isinstance(value, dict):
        out = {k: v for k, v in value.items() if k in ("name", "type", "notes", "required")}
        out.setdefault("type", "text")
        out["required"] = bool(out.get("required"))
        return out
    name, _, kind = str(value).partition(":")
    return {"name": name.strip(), "type": kind.strip() or "text"}


def _message(value) -> dict | None:
    if isinstance(value, dict) and value.get("source") and value.get("target"):
        return {"source": str(value["source"]), "target": str(value["target"]), "text": str(value.get("text") or "")}
    match = re.match(r"\s*(.+?)\s*-+>>?\s*(.+?)\s*:\s*(.+)", str(value)) if isinstance(value, str) else None
    return {"source": match[1], "target": match[2], "text": match[3]} if match else None


def sds_part(data: dict, part: str) -> dict:
    """One pass of the SDS: normalised like a whole SDS, then only the part's keys."""
    from controller.app.project_docs import SDS_PARTS
    keys = (*SDS_PARTS[part], "feedback_replies")
    full = sds({**{k: v for k, v in data.items() if k in keys}})
    return {k: v for k, v in full.items() if k in keys}


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
SDS_ROWS = ("traceability", "objects", "screens", "components", "stack", "modules", "tables", "deployment", "zones", "links",
            "errors", "threats", "authorization", "nfr", "metrics", "conventions", "integrations")
KEEP_LISTS = {"actors", "steps", "alternatives", "negatives", "stories", "requirements", "states", "changes",
              "options", "refs", "layout", "calls", "fields", "errors", "transitions", "participants", "messages",
              "flow"}


def _flatten_rows(rows) -> list:
    out = []
    for row in rows if isinstance(rows, list) else []:
        if isinstance(row, dict):
            row = {k: (", ".join(str(x) for x in v if x not in (None, "")) if isinstance(v, list) and k not in KEEP_LISTS
                       else _sentence(v) if isinstance(v, dict) and k not in KEEP_LISTS else v) for k, v in row.items()}
        out.append(row)
    return out
