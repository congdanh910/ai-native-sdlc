"""Cross-artifact analysis before and after coding, like /speckit-analyze in the company process.

Before coding (at the Quality gate): do the spec, plan, tasks and test cases agree? After coding (before
the pull request): did the change stay inside the plan and is every task proven? Findings are Critical,
High, Medium or Low; Critical and High block the gate until the artifacts are revised.
"""

from controller.app.projects.config import matches_any
from controller.app.schemas import Criterion, PlanPayload, TestPlan

BLOCKING = ("Critical", "High")


def _finding(severity: str, category: str, text: str, fix: dict | None = None) -> dict:
    """fix: the next step for the person at the gate (guidance.fix), when the finding blocks it."""
    return {"severity": severity, "category": category, "text": text, **({"fix": fix} if fix else {})}


def before_coding(criteria: list[Criterion], plan: PlanPayload, tests: TestPlan) -> list[dict]:
    from controller.app.guidance import fix
    findings = []
    delivered = {a for t in plan.tasks for a in t.acceptance_criteria}
    tested = {c.criterion for c in tests.cases}
    known = {c.id for c in criteria}
    for criterion in criteria:
        if criterion.id not in delivered:
            findings.append(_finding("Critical", "coverage", f"{criterion.id} is delivered by no task", fix(
                "replan", f"No task of the plan says it delivers {criterion.id}, so no task would build it.",
                f"Add {criterion.id} ({criterion.text}) to the acceptance criteria of the task that builds it.")))
        if criterion.id not in tested:
            findings.append(_finding("High", "coverage", f"{criterion.id} has no test case", fix(
                "redesign_tests", f"Nothing would prove {criterion.id} before the pull request.",
                f"Add a test case for {criterion.id} ({criterion.text}) on a task that delivers it.")))
    for task in plan.tasks:
        unknown = [a for a in task.acceptance_criteria if a not in known]
        if unknown:
            findings.append(_finding("High", "consistency", f"{task.id} delivers {', '.join(unknown)}, which the spec does not have", fix(
                "replan", f"{task.id} names criteria the accepted spec does not have.",
                f"Remove {', '.join(unknown)} from {task.id}: the spec has no such criterion.")))
        if not any(c.task_id == task.id for c in tests.cases):
            findings.append(_finding("Medium", "coverage", f"{task.id} has no test case; its change is proven only by other tasks' tests"))
    tasks = {t.id: t for t in plan.tasks}

    def delivered_by(task_id: str, seen: set[str]) -> set[str]:
        """Criteria of the task and of every task it depends on: an integration test on a later task may prove
        what an earlier task built, since the later task runs on top of it."""
        if task_id in seen or task_id not in tasks:
            return set()
        seen.add(task_id)
        out = set(tasks[task_id].acceptance_criteria)
        for dependency in tasks[task_id].depends_on:
            out |= delivered_by(dependency, seen)
        return out

    for case in tests.cases:
        if case.task_id in tasks and case.criterion not in delivered_by(case.task_id, set()):
            task = tasks[case.task_id]
            owners = [t.id for t in plan.tasks if case.criterion in t.acceptance_criteria]
            builds = [p for p in [*task.modify, *task.create]
                      if p.split("/")[0] == case.target_file.split("/")[0]]  # the test's part of the code (backend, web)
            if builds or not owners:  # the task builds what the test proves: the plan forgot the criterion on it
                next_step = fix("replan",
                                f"{case.id} tests {case.criterion} in {case.target_file}, which is {case.task_id}'s part of "
                                f"the code ({', '.join(builds[:3]) or 'its files'}), but the plan lists {case.criterion} only on "
                                f"{', '.join(owners) or 'no task'}. The test is right; the plan misses the criterion on {case.task_id}.",
                                f"Add {case.criterion} to {case.task_id}'s acceptance criteria: {case.task_id} builds what "
                                f"{case.id} proves ({case.target_file}). Keep the rest of the plan as it is.")
                next_step["amend"] = {"task": case.task_id, "criterion": case.criterion}  # fixable without a model
            else:  # the test sits on the wrong task
                next_step = fix("redesign_tests",
                                f"{case.id} is put on {case.task_id}, which neither builds {case.criterion} nor runs after "
                                f"a task that does ({', '.join(owners)}).",
                                f"Move {case.id} to {owners[-1]}, which delivers {case.criterion}, or prove "
                                f"{case.criterion} only through {owners[-1]}'s tests.")
            findings.append(_finding("High", "consistency", f"{case.id} proves {case.criterion} on {case.task_id}, "
                                                            "which neither delivers it nor depends on a task that does",
                                     next_step))
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
                 test_paths: list[str], documents: tuple[str, ...] = ("specs/", ".sdlc/", "SRS.md", "SDS.md"),
                 also_allowed: list[str] = ()) -> list[dict]:
    """also_allowed: what the tasks added after the plan may change (a fix task for a person's defects, the task that
    resolves conflicts with main; their files are patterns of the allowed paths), so their changes are planned too."""
    findings = []
    for task in plan.tasks:
        status = statuses.get(task.id, "missing")
        if status != "PASSED":
            findings.append(_finding("Critical", "delivery", f"{task.id} is {status}, not PASSED"))
    planned = {p for t in plan.tasks for p in [*t.modify, *t.create]}
    test_files = {c.target_file for c in tests.cases}
    for path in changed:
        if path.startswith(documents) or matches_any(path, test_paths) or path in planned or path in test_files \
                or (also_allowed and matches_any(path, list(also_allowed))):
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
