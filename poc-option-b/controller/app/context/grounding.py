"""Deterministic plan grounding check, run before a human sees the plan."""

from controller.app.context.builder import ContextBundle
from controller.app.projects.config import EffectivePolicy, matches_any
from controller.app.schemas import Criterion, PlanPayload, TestPlan


def check_plan(plan: PlanPayload, tests: TestPlan, criteria: list[Criterion], policy: EffectivePolicy,
               files: set[str], symbols: dict[str, set[str]]) -> dict:
    findings: list[str] = []
    criterion_ids = {c.id for c in criteria}
    task_ids = {task.id for task in plan.tasks}
    validations = {command.name for command in policy.validation}
    changed: set[str] = set()
    by_id = {task.id: task for task in plan.tasks}

    def provided_before(task_id: str, seen: set[str] | None = None) -> set[str]:
        """Symbols added by the task itself or by any task it depends on, directly or not."""
        seen = seen if seen is not None else set()
        if task_id in seen or task_id not in by_id:
            return set()
        seen.add(task_id)
        names = set(by_id[task_id].provides)
        for dependency in by_id[task_id].depends_on:
            names |= provided_before(dependency, seen)
        return names

    def created_before(task_id: str, seen: set[str] | None = None) -> set[str]:
        """Files created by the tasks this task depends on, directly or not (not by the task itself)."""
        seen = seen if seen is not None else set()
        paths: set[str] = set()
        for dependency in by_id[task_id].depends_on if task_id in by_id else []:
            if dependency in seen or dependency not in by_id:
                continue
            seen.add(dependency)
            paths |= set(by_id[dependency].create) | created_before(dependency, seen)
        return paths

    if len(plan.tasks) > policy.max_tasks:
        findings.append(f"{len(plan.tasks)} tasks exceed the limit of {policy.max_tasks}")
    for task in plan.tasks:
        earlier = created_before(task.id)
        for path in task.modify:
            if path not in files and path not in earlier:  # a task it depends on may create it first
                findings.append(f"{task.id} modifies {path}, which neither exists at the base commit nor is "
                                "created by a task it depends on")
        for path in task.create:
            if path in files:
                findings.append(f"{task.id} creates {path}, which already exists; use modify")
        for path in task.modify + task.create:
            changed.add(path)
            if matches_any(path, policy.protected_paths):
                findings.append(f"{task.id} touches protected path {path}")
            elif not matches_any(path, policy.allowed_paths):
                if matches_any(path, policy.test_paths):
                    findings.append(f"{task.id} changes test file {path}; tests belong to the test plan")
                else:
                    findings.append(f"{task.id} changes {path}, outside the allowed paths {policy.allowed_paths}")
        if not task.modify and not task.create:
            findings.append(f"{task.id} changes no file")
        for reference in task.references:
            if reference.file not in files and reference.file not in created_before(task.id):
                findings.append(f"{task.id} references {reference.file}, which does not exist and no task it "
                                f"depends on creates")
            elif (reference.symbol and reference.symbol not in symbols.get(reference.file, set())
                  and reference.symbol not in provided_before(task.id)):
                findings.append(f"{task.id} references {reference.symbol} in {reference.file}, which is not defined there")
        for criterion in task.acceptance_criteria:
            if criterion not in criterion_ids:
                findings.append(f"{task.id} lists unknown acceptance criterion {criterion}")
        for name in task.validation:
            if name not in validations:
                findings.append(f"{task.id} uses unknown validation {name}; configured: {sorted(validations)}")
    if len(changed) > policy.max_changed_files:
        findings.append(f"{len(changed)} files to change exceed the limit of {policy.max_changed_files}")

    delivered = {c for task in plan.tasks for c in task.acceptance_criteria}
    for criterion in sorted(criterion_ids - delivered):
        findings.append(f"no task delivers {criterion}")
    tested = set()
    for case in tests.cases:
        if case.task_id not in task_ids:
            findings.append(f"{case.id} belongs to unknown task {case.task_id}")
        if case.criterion not in criterion_ids:
            findings.append(f"{case.id} proves unknown criterion {case.criterion}")
        tested.add(case.criterion)
        if not matches_any(case.target_file, policy.test_paths):
            findings.append(f"{case.id} targets {case.target_file}, outside the test paths {policy.test_paths}")
    for criterion in sorted(criterion_ids - tested):
        findings.append(f"no test case proves {criterion}")
    findings = list(dict.fromkeys(findings))  # one line per problem, not one per reference
    return {"status": "fail" if findings else "pass", "findings": findings,
            "warnings": _warnings(changed, files, policy)}


MARKUP = (".html", ".htm", ".vue", ".svelte", ".jsx", ".tsx")
STYLES = (".css", ".scss", ".sass", ".less")


def _warnings(changed: set[str], files: set[str], policy: EffectivePolicy) -> list[str]:
    """Advice for the human approver; never fails the plan."""
    warnings = []
    stylesheets = sorted(f for f in files if f.endswith(STYLES) and matches_any(f, policy.allowed_paths))
    markup = sorted(f for f in changed if f.endswith(MARKUP))
    if markup and stylesheets and not any(f.endswith(STYLES) for f in changed):
        warnings.append(f"the plan changes markup ({', '.join(markup)}) but no stylesheet "
                        f"({', '.join(stylesheets)}); the Developer may only style new elements inline")
    return warnings


def check_with_bundle(plan: PlanPayload, tests: TestPlan, criteria: list[Criterion], policy: EffectivePolicy,
                      bundle: ContextBundle) -> dict:
    return check_plan(plan, tests, criteria, policy, set(bundle.files), bundle.known_symbols())
