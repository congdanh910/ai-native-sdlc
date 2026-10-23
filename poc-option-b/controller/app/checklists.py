"""The review checklist at each gate, after aif-review-checklist.md, in three kinds:

- controller: checked by code; a failure blocks the approval (the human can revise or reject instead)
- ai: the role's own self-check, submitted with its artifact (pass / deferred / fail / n/a, with a note)
- human: judgment only a person can give; every item must be ticked to approve

The approval stores what was checked, by whom and on which artifact version, as audit evidence.
"""

from dataclasses import dataclass
import re

from controller.app.schemas import Criterion, PlanPayload, TestPlan, parse_criteria

BANNED = ("fast", "user-friendly", "appropriate", "relevant", "seamless", "optimal", "as needed", "if possible")
PLACEHOLDER = re.compile(r"\bTBD\b|\[(?:Entity|Feature Name|ROLE|ABBR|YYYY-MM-DD)\]|NEEDS CLARIFICATION", re.I)
SECRET = re.compile(r"""(?i)\b(?:password|passwd|secret|api[_-]?key|token)\b\s*[:=]\s*["'][^"'\s]{4,}["']""")
TECH_IN_CRITERION = re.compile(r"(?:\b(?:GET|POST|PUT|PATCH|DELETE)\s+/|/api/|\bSELECT\b.+\bFROM\b|\.(?:js|ts|py|java|sql)\b)")


@dataclass(frozen=True)
class Item:
    id: str
    text: str


# ---------------------------------------------------------------- items per gate

AI_ITEMS = {
    "spec": [
        Item("testable", "Every AC describes a testable, observable outcome, not an implementation detail"),
        Item("what-not-how", "No technology names, endpoint paths, table or function names in ACs"),
        Item("terminology", "Business terms match agents_md and the project constitution"),
        Item("trace", "The story traces to an SRS entity (section 2) or a business flow step (section 6)"),
        Item("scenarios", "ACs cover the full scenario: success, validation errors, access denied, form states"),
        Item("edge-cases", "Every edge case category was scanned; the ones that apply are listed"),
    ],
    "plan": [
        Item("preflight", "Spec gaps were flagged, and none blocks the design"),
        Item("layering", "Rules and business logic sit where the project's architecture puts them"),
        Item("data-model", "Domain objects are distinct from the SRS entities and named in their language"),
        Item("sequence", "Sequence diagrams follow the call chain, label intents, and show error flows"),
        Item("api", "Endpoints and errors follow the project's API standards, or there is no endpoint change"),
        Item("security", "Every protected action states who may do it; secrets and passwords are handled safely"),
        Item("sync", "SDS sections the design changes are named, or there are none"),
    ],
    "tests": [
        Item("unhappy", "Unhappy paths are covered: validation errors, rejected states, guardrails"),
        Item("edges", "Edge cases are covered: boundary values, empty input, concurrent or duplicate actions"),
        Item("fresh-data", "Each test creates the data it needs; nothing relies on seeded data"),
        Item("helpers", "Existing test helpers are reused, not duplicated"),
    ],
}

HUMAN_ITEMS = {
    "baseline": [
        Item("agreed-scope", "The scope matches what was agreed with the customer for this phase"),
        Item("deferrals", "The items out of scope and the deferred questions are acceptable"),
        Item("actors", "The actors and their permissions are complete"),
    ],
    "acceptance": [
        Item("tried", "I tried each acceptance criterion on the preview with the demo account of its role"),
        Item("evidence", "The screenshots and videos show what each criterion asks"),
        Item("no-blocker", "No defect outside the criteria blocks this story"),
    ],
    "onboarding": [
        Item("stack", "AGENTS.md states the stack, commands, structure and rules of the SDS"),
        Item("paths", "The paths the coding roles may change and the test paths fit the structure"),
        Item("environment", "The validation environment (image, services, setup) is acceptable and holds no real secret"),
    ],
    "ui": [
        Item("tried", "I opened the prototype screens of each concept on the target devices"),
        Item("fits-users", "The chosen concept fits the roles, devices and brand of the product"),
        Item("readable", "Text and controls are readable (the contrast checks pass, or the exceptions are acceptable)"),
    ],
    "options": [
        Item("meets-nfr", "The chosen option meets the quality targets and constraints of the SRS"),
        Item("budget", "Its cost and operations fit the budget and the team that will run it"),
        Item("data", "Where personal and sensitive data lives is acceptable"),
    ],
    "srs": [
        Item("matches-customer", "Requirements match what the customer asked for (brief, notes, meetings)"),
        Item("complete", "Roles, features and stories are complete for this release"),
        Item("scope", "Out of scope and assumptions are agreed"),
    ],
    "sds": [
        Item("fits-constraints", "The architecture fits the stack, deployment and budget constraints"),
        Item("decisions", "The decisions in section 9 are acceptable"),
        Item("security", "Security and personal data handling are acceptable"),
    ],
    "spec": [
        Item("full-scenario", "The ACs cover the whole scenario the Product Owner expects"),
        Item("clear", "The ACs are clear enough for the whole team to share one understanding"),
        Item("deferrals", "Every deferred [~] checklist item is acceptable to postpone"),
    ],
    "plan": [
        Item("scope", "The design covers this story only; no scope creep"),
        Item("architecture", "The plan follows the project's architecture and constitution"),
        Item("tasks", "Tasks are small, ordered and name the files they change"),
    ],
    "tests": [
        Item("intent", "The test cases match what the spec means, not only its words"),
        Item("no-extra", "There are no redundant or out-of-scope test cases"),
        Item("missing", "No scenario is missing"),
    ],
    "pr": [
        Item("matches-plan", "The code matches the plan; any deviation is explained"),
        Item("review", "Every important review finding is fixed or answered"),
        Item("docs", "The story's sections in specs/ are up to date"),
    ],
}


# ---------------------------------------------------------------- controller checks

def _result(item_id: str, text: str, problems: list[str], fixes: list[dict] | None = None) -> dict:
    """fixes: next steps for the person when the check fails (guidance)."""
    return {"id": item_id, "text": text, "ok": not problems, "detail": "; ".join(problems[:5]),
            **({"fixes": fixes} if problems and fixes else {})}


def with_fixes(gate: str, results: list[dict]) -> list[dict]:
    """The standard next step of each failing check that has none of its own."""
    from controller.app.guidance import check_fixes
    return [r if r["ok"] or r.get("fixes") else {**r, "fixes": check_fixes(gate, r["id"], r["detail"])} for r in results]


def check_criteria(criteria: list[Criterion]) -> list[dict]:
    """Spec gate: ids, Given / When / Then, banned words, short names, no technology in ACs."""
    results = []
    problems = []
    for prefix in ("AC", "EC"):
        ids = [c.id for c in criteria if c.id.startswith(prefix + "-")]
        expected = [f"{prefix}-{n:02d}" for n in range(1, len(ids) + 1)]
        if ids and ids != expected:
            problems.append(f"{prefix} ids are {', '.join(ids)}; expected {', '.join(expected)}")
    results.append(_result("ids", "AC and EC ids are 2 digits, start at 01 and have no gaps", problems))
    missing = [c.id for c in criteria if not (c.given and c.when and c.then)]
    results.append(_result("gwt", "Every AC and EC has Given, When and Then", [f"{', '.join(missing)} lack a clause"] if missing else []))
    banned = [f"{c.id} uses '{word}'" for c in criteria for word in BANNED
              if re.search(rf"\b{re.escape(word)}\b", c.full, re.I)]
    results.append(_result("banned-words", f"No vague words ({', '.join(BANNED)})", banned))
    names = [c.text.lower() for c in criteria]
    duplicates = sorted({n for n in names if names.count(n) > 1})
    results.append(_result("names", "Short names are unique within the story", [f"repeated: {', '.join(duplicates)}"] if duplicates else []))
    tech = [c.id for c in criteria if TECH_IN_CRITERION.search(c.full)]
    results.append(_result("no-paths", "No API paths, queries or file names in ACs", [f"{', '.join(tech)} name code"] if tech else []))
    return results


def check_spec(markdown: str) -> list[dict]:
    try:
        criteria = parse_criteria(markdown)
    except ValueError as exc:
        return with_fixes("spec", [_result("parse", "The spec lists its criteria in the template format", [str(exc)])])
    stories = len(re.findall(r"\*\*As(?: an?)?\*\*", markdown.split("### Business Analyst notes")[0]))
    results = [_result("one-story", "The section holds exactly one user story statement (**As a** ...)",
                       [] if stories == 1 else [f"found {stories} statements"])]
    return with_fixes("spec", results + check_criteria(criteria))


def check_plan(plan: PlanPayload, criteria: list[Criterion], grounding: dict | None, test_paths) -> list[dict]:
    from controller.app.projects.config import matches_any  # avoid a cycle at import time
    delivered = {a for t in plan.tasks for a in t.acceptance_criteria}
    missing = [c.id for c in criteria if c.id not in delivered]
    no_files = [t.id for t in plan.tasks if not (t.modify or t.create)]
    tests = [f"{t.id}: {p}" for t in plan.tasks for p in [*t.modify, *t.create] if matches_any(p, test_paths)]
    text = " ".join([plan.summary, *[t.title for t in plan.tasks]])
    findings = (grounding or {}).get("findings", [])
    return with_fixes("plan", [
        _result("delivered", "Every AC and EC is delivered by at least one task", [f"not delivered: {', '.join(missing)}"] if missing else []),
        _result("files", "Every task names the files it changes", [f"no files: {', '.join(no_files)}"] if no_files else []),
        _result("no-tests", "No task changes test files; tests are the QA Engineer's", tests),
        _result("placeholders", "No placeholders such as TBD remain", ["placeholder text in the plan"] if PLACEHOLDER.search(text) else []),
        _result("grounding", "Grounding: every file, function and import the plan names exists or an earlier task creates it", findings if (grounding or {}).get("status") == "fail" else []),
    ])


def check_tests(tests: TestPlan, plan: PlanPayload, criteria: list[Criterion], test_paths) -> list[dict]:
    from controller.app.projects.config import matches_any
    ids = {c.id for c in criteria}
    uncovered = [c.id for c in criteria if not any(t.criterion == c.id for t in tests.cases)]
    unknown = sorted({t.criterion for t in tests.cases} - ids)
    tasks = {t.id for t in plan.tasks}
    orphans = sorted({t.task_id for t in tests.cases} - tasks)
    outside = [f"{t.id}: {t.target_file}" for t in tests.cases if not matches_any(t.target_file, test_paths)]
    empty = [t.id for t in tests.cases if not (t.description.strip() and t.expected.strip())]
    from controller.app.analysis import before_coding, blocking
    found = blocking(before_coding(criteria, plan, tests))
    serious = [f"{f['severity']}: {f['text']}" for f in found]
    return with_fixes("tests", [
        _result("analysis", "Cross-artifact analysis finds no Critical or High issue (spec, plan, tasks, tests)", serious,
                [{**f["fix"], "finding": f["text"]} for f in found if f.get("fix")]),
        _result("coverage", "Every AC and EC has at least one test case", [f"no test: {', '.join(uncovered)}"] if uncovered else []),
        _result("known-criteria", "Every test case names an AC or EC of this story", [f"unknown: {', '.join(unknown)}"] if unknown else []),
        _result("known-tasks", "Every test case names a task of the approved plan", [f"unknown: {', '.join(orphans)}"] if orphans else []),
        _result("test-paths", "Test files are inside the project's test paths", outside),
        _result("fields", "Every test case has a scenario and an expected result", [f"empty: {', '.join(empty)}"] if empty else []),
    ])


def check_release(task_statuses: dict[str, str], added_lines: list[str], other_sections_changed: list[str],
                  analysis: list[dict] | None = None) -> list[dict]:
    failing = [t for t, s in task_statuses.items() if s != "PASSED"]
    secrets = [line.strip()[:80] for line in added_lines if SECRET.search(line)]
    serious = [f"{f['severity']}: {f['text']}" for f in (analysis or []) if f["severity"] in ("Critical", "High")]
    return [
        _result("analysis", "The analysis after coding finds no Critical or High issue", serious),
        _result("tasks", "Every task passed validation", [f"not passed: {', '.join(failing)}"] if failing else []),
        _result("secrets", "No hard-coded passwords, tokens or keys in the change", secrets),
        _result("other-stories", "Other stories' sections in specs/ are unchanged", other_sections_changed),
    ]


def ai_results(items: list[Item], answers: list[dict]) -> list[dict]:
    """The role's self-check, item by item; an item it did not answer shows as not checked."""
    by_id = {a.get("id"): a for a in answers if isinstance(a, dict)}
    out = []
    for item in items:
        answer = by_id.get(item.id) or {}
        status = str(answer.get("status") or "not checked").lower()
        out.append({"id": item.id, "text": item.text, "status": status, "note": answer.get("note", "")})
    return out


def human_items(gate: str) -> list[dict]:
    return [{"id": i.id, "text": i.text} for i in HUMAN_ITEMS.get(gate, [])]


# Added to the acceptance gate when the project has a UI guide (docs/ui/UI.md) and ui.review is gated.
UI_REVIEW = Item("ui-concept", "The screens follow the UI concept in docs/ui/UI.md (tokens, components, states)")


def missing_ticks(gate: str, checked: list[str] | None, human: list[dict] | None = None) -> list[str]:
    ticked = set(checked or [])
    items = human if human is not None else human_items(gate)
    return [i["text"] for i in items if i["id"] not in ticked]


# The spec-kit requirements checklist (checklists/requirements.md), filled from the spec gate's self-check.
REQUIREMENT_SOURCES = {
    "No implementation details (languages, frameworks, APIs)": "what-not-how",
    "Requirements are testable and unambiguous": "testable",
    "Success criteria are measurable": "testable",
    "Success criteria are technology-agnostic (no implementation details)": "what-not-how",
    "All acceptance scenarios are defined": "scenarios",
    "Edge cases are identified": "edge-cases",
    "User scenarios cover primary flows": "scenarios",
    "No implementation details leak into specification": "what-not-how",
}


def requirement_statuses(self_check: list[dict]) -> dict[str, tuple[str, str]]:
    """Item -> (status, note). The human approved the spec gate, so an item the Business Analyst did not pass is
    recorded as deferred with its note rather than left open; items with no self-check source passed the
    controller's structural checks."""
    from controller.app.feature_docs import REQUIREMENT_ITEMS  # avoid a cycle at import time
    answers = {a["id"]: a for a in self_check}
    out = {}
    for items in REQUIREMENT_ITEMS.values():
        for item in items:
            source = REQUIREMENT_SOURCES.get(item)
            answer = answers.get(source) if source else None
            if source is None or (answer and answer["status"] in ("pass", "n/a")):
                out[item] = ("pass", "")
            else:
                note = (answer or {}).get("note") or "not self-checked by the Business Analyst"
                out[item] = ("deferred", f"accepted at the spec gate: {note}")
    return out
