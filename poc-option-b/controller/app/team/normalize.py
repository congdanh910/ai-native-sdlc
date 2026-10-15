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


def spec(data: dict) -> dict:
    data = dict(data)
    criteria = []
    for index, item in enumerate(data.get("acceptance_criteria") or [], start=1):
        if isinstance(item, str):
            item = {"id": f"AC-{index}", "text": item}
        elif isinstance(item, dict) and "id" not in item:
            item = {"id": f"AC-{index}", **item}
        criteria.append(item)
    data["acceptance_criteria"] = criteria
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
    return data


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
        cases.append(case)
    data["cases"] = cases
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
    for feature in data["features"]:
        if isinstance(feature, dict):
            feature["requirements"] = as_list(feature.get("requirements"))
            for story in as_list(feature.get("stories")):
                if isinstance(story, dict) and isinstance(story.get("actors"), str):
                    story["actors"] = [a.strip() for a in story["actors"].split(",") if a.strip()]
            feature["stories"] = as_list(feature.get("stories"))
    for flow in data["flows"]:
        if isinstance(flow, dict):
            for key in ("steps", "alternatives", "negatives"):
                flow[key] = as_list(flow.get(key))
    return data
