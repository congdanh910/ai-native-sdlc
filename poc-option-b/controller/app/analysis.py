"""Cross-artifact analysis before and after coding, like /speckit-analyze in the company process.

Before coding (at the Quality gate): do the spec, plan, tasks and test cases agree? After coding (before
the pull request): did the change stay inside the plan and is every task proven? Findings are Critical,
High, Medium or Low; Critical and High block the gate until the artifacts are revised.
"""

from controller.app.projects.config import matches_any
from controller.app.schemas import Criterion, PlanPayload, TestPlan

BLOCKING = ("Critical", "High")


def _finding(severity: str, category: str, text: str) -> dict:
    return {"severity": severity, "category": category, "text": text}


def before_coding(criteria: list[Criterion], plan: PlanPayload, tests: TestPlan) -> list[dict]:
    findings = []
    delivered = {a for t in plan.tasks for a in t.acceptance_criteria}
    tested = {c.criterion for c in tests.cases}
    known = {c.id for c in criteria}
    for criterion in criteria:
        if criterion.id not in delivered:
            findings.append(_finding("Critical", "coverage", f"{criterion.id} is delivered by no task"))
        if criterion.id not in tested:
            findings.append(_finding("High", "coverage", f"{criterion.id} has no test case"))
    for task in plan.tasks:
        unknown = [a for a in task.acceptance_criteria if a not in known]
        if unknown:
            findings.append(_finding("High", "consistency", f"{task.id} delivers {', '.join(unknown)}, which the spec does not have"))
        if not any(c.task_id == task.id for c in tests.cases):
            findings.append(_finding("Medium", "coverage", f"{task.id} has no test case; its change is proven only by other tasks' tests"))
    by_task = {t.id: set(t.acceptance_criteria) for t in plan.tasks}
    for case in tests.cases:
        if case.task_id in by_task and case.criterion not in by_task[case.task_id]:
            findings.append(_finding("High", "consistency",
                                     f"{case.id} proves {case.criterion} on {case.task_id}, which does not deliver it"))
    texts = {}
    for criterion in criteria:
        key = " ".join(criterion.full.lower().split())
        if key in texts:
            findings.append(_finding("Medium", "duplication", f"{criterion.id} repeats {texts[key]}"))
        texts.setdefault(key, criterion.id)
    for task in plan.tasks:
        for dependency in task.depends_on:
            later = [t.id for t in plan.tasks].index(dependency) > [t.id for t in plan.tasks].index(task.id)
            if later:
                findings.append(_finding("Low", "ordering", f"{task.id} depends on {dependency}, which comes later in the plan"))
    return findings


def after_coding(plan: PlanPayload, tests: TestPlan, statuses: dict[str, str], changed: list[str],
                 test_paths: list[str], documents: tuple[str, ...] = ("specs/", ".sdlc/", "SRS.md", "SDS.md")) -> list[dict]:
    findings = []
    for task in plan.tasks:
        status = statuses.get(task.id, "missing")
        if status != "PASSED":
            findings.append(_finding("Critical", "delivery", f"{task.id} is {status}, not PASSED"))
    planned = {p for t in plan.tasks for p in [*t.modify, *t.create]}
    test_files = {c.target_file for c in tests.cases}
    for path in changed:
        if path.startswith(documents) or matches_any(path, test_paths) or path in planned or path in test_files:
            continue
        findings.append(_finding("High", "scope", f"{path} changed but no task plans it"))
    untouched = sorted(planned - set(changed))
    for path in untouched:
        findings.append(_finding("Medium", "scope", f"{path} is planned but unchanged"))
    for path in sorted(test_files - set(changed)):
        findings.append(_finding("High", "tests", f"{path} holds planned test cases but was not changed"))
    return findings


def blocking(findings: list[dict]) -> list[dict]:
    return [f for f in findings if f["severity"] in BLOCKING]


def render(title: str, stage: str, findings: list[dict]) -> str:
    counts = {s: sum(1 for f in findings if f["severity"] == s) for s in ("Critical", "High", "Medium", "Low")}
    lines = [f"# Analysis {stage}: {title}", "",
             " · ".join(f"**{n}** {s}" for s, n in counts.items()), ""]
    if not findings:
        return "\n".join(lines + ["No findings: spec, plan, tasks and test cases agree.", ""])
    lines += ["| Severity | Category | Finding |", "|---|---|---|"]
    order = {"Critical": 0, "High": 1, "Medium": 2, "Low": 3}
    for f in sorted(findings, key=lambda f: order[f["severity"]]):
        lines.append(f"| {f['severity']} | {f['category']} | {f['text']} |")
    return "\n".join(lines) + "\n"
