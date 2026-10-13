"""Deterministic clean-up of a model's plan and test plan before grounding.

Prompts alone did not stop Qwen3 from planning test-file tasks or numbering test
cases against tasks that do not exist (eval 3). These are policy rules, so the
coordinator enforces them in code and records every change it made. Nothing is
invented: paths and tasks are only removed, and a test case is only moved to a
task that already delivers the criterion it proves.
"""

from controller.app.projects.config import EffectivePolicy, matches_any
from controller.app.schemas import PlanPayload, TestPlan


def tidy_plan(plan: PlanPayload, policy: EffectivePolicy) -> tuple[PlanPayload, list[str]]:
    """Drop test files from the Tech Lead's tasks, drop tasks left with no file, renumber."""
    notes: list[str] = []
    tasks = [task.model_dump() for task in plan.tasks]
    for task in tasks:
        for key in ("modify", "create"):
            tests = [p for p in task[key] if matches_any(p, policy.test_paths) and not matches_any(p, policy.allowed_paths)]
            if tests:
                task[key] = [p for p in task[key] if p not in tests]
                notes.append(f"{task['id']}: removed test file(s) {', '.join(tests)}; tests belong to the test plan")
    dropped = {task["id"]: task for task in tasks if not task["modify"] and not task["create"]}
    if not dropped:
        return (PlanPayload.model_validate({**plan.model_dump(), "tasks": tasks}) if notes else plan), notes
    if len(dropped) == len(tasks):
        notes.append("every task was left without a source file; kept the plan for grounding to report")
        return plan, notes
    for task_id, task in dropped.items():
        notes.append(f"{task_id}: dropped \"{task['title']}\" because it changes no source file")

    def inherited(task_id: str, seen: set[str]) -> list[str]:
        """Dependencies of a dropped task pass on to the tasks that depended on it."""
        if task_id not in dropped or task_id in seen:
            return [task_id]
        seen.add(task_id)
        return [d for dep in dropped[task_id]["depends_on"] for d in inherited(dep, seen)]

    kept = [task for task in tasks if task["id"] not in dropped]
    renumber = {task["id"]: f"T{index}" for index, task in enumerate(kept, start=1)}
    for task in kept:
        depends = []
        for dep in task["depends_on"]:
            for target in inherited(dep, set()):
                if target in renumber and renumber[target] not in depends:
                    depends.append(renumber[target])
        task["id"], task["depends_on"] = renumber[task["id"]], depends
    if any(old != new for old, new in renumber.items()):
        notes.append("renumbered tasks: " + ", ".join(f"{old}→{new}" for old, new in renumber.items() if old != new))
    return PlanPayload.model_validate({**plan.model_dump(), "tasks": kept}), notes


def tidy_tests(tests: TestPlan, plan: PlanPayload) -> tuple[TestPlan, list[str]]:
    """Move a case that names an unknown task to the first task that delivers its criterion."""
    notes: list[str] = []
    known = {task.id for task in plan.tasks}
    cases = [case.model_dump() for case in tests.cases]
    for case in cases:
        if case["task_id"] in known:
            continue
        owner = next((task.id for task in plan.tasks if case["criterion"] in task.acceptance_criteria), None)
        if owner:
            notes.append(f"{case['id']}: moved from unknown task {case['task_id']} to {owner}, "
                         f"which delivers {case['criterion']}")
            case["task_id"] = owner
    return (TestPlan.model_validate({"cases": cases}) if notes else tests), notes
