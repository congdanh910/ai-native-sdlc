"""Deterministic stand-ins for every AI role, so the whole loop can be exercised in B2.

The mock reads the real intent, spec and repository file list, so its outputs
follow the request, but it calls no model and changes no code.
Write `[blocking]` before an open question in the intent to see the
clarification loop.
"""

import re

from controller.app.projects.config import matches_any
from controller.app.schemas import (Criterion, Intent, PlanPayload, PlanTask, Question, Review, Spec, TestCase,
                                    TestPlan, parse_criteria)
from controller.app.team.base import ReleaseResult, RunContext, StepResult


def _section(markdown: str, heading: str) -> str:
    match = re.search(rf"^## {re.escape(heading)}\n\n(.*?)(?=^## |\Z)", markdown, re.MULTILINE | re.DOTALL)
    text = match[1].strip() if match else ""
    return "" if text == "_None._" else text


def _items(text: str) -> list[str]:
    lines = [line.strip().lstrip("-*0123456789. ").strip() for line in text.splitlines()]
    return [line for line in lines if line]


class MockTeam:
    engine = "mock"
    last_call = None

    def draft_intent(self, text: str, title: str) -> Intent:
        sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s.strip()]
        return Intent(title=title, problem=sentences[0] if sentences else text,
                      outcome="\n".join(f"- {s}" for s in sentences[1:]) or f"- {text.strip()}",
                      open_questions="")

    def specify(self, context: RunContext) -> Spec:
        outcome = _items(_section(context.intent_md, "Desired outcome")) or [context.title]
        answered = {question for question, _ in context.answers}
        questions = []
        for line in _items(_section(context.intent_md, "Open questions")):
            blocking = line.lower().startswith("[blocking]")
            text = line[len("[blocking]"):].strip() if blocking else line
            if text not in answered:
                questions.append(Question(question=text, blocking=blocking))
        criteria = [Criterion(id=f"AC-{i}", text=text) for i, text in enumerate(outcome, start=1)]
        assumptions = [f"Answer to '{q}': {a}" for q, a in context.answers]
        return Spec(goal=_section(context.intent_md, "Problem") or context.title,
                    user_stories=[f"As a user, I want {text[0].lower() + text[1:]}" for text in outcome],
                    acceptance_criteria=criteria, assumptions=assumptions,
                    concerns=["Mock Business Analyst: no model was called."], questions=questions)

    def plan(self, context: RunContext) -> PlanPayload:
        criteria = parse_criteria(context.spec_md or "")
        sources = [f for f in context.files if matches_any(f, context.policy.allowed_paths)
                   and not matches_any(f, context.policy.protected_paths)]
        validation = [command.name for command in context.policy.validation]
        tasks = []
        for index, criterion in enumerate(criteria[: context.policy.max_tasks], start=1):
            target = sources[(index - 1) % len(sources)] if sources else None
            tasks.append(PlanTask(
                id=f"T{index}", title=f"Implement {criterion.id}: {criterion.text[:80]}",
                depends_on=[f"T{index - 1}"] if index > 1 else [],
                modify=[target] if target else [], acceptance_criteria=[criterion.id], validation=validation))
        return PlanPayload(summary=f"Mock plan: one task per acceptance criterion ({len(tasks)} tasks).",
                           tasks=tasks)

    def design_tests(self, context: RunContext, plan: PlanPayload) -> TestPlan:
        test_dir = context.policy.test_paths[0].split("*")[0].rstrip("/") or "tests"
        cases = [TestCase(id=f"TC-{task.id}-{n}", task_id=task.id, criterion=criterion,
                          description=f"Checks {criterion} for {task.id}", expected="test passes after the task",
                          target_file=f"{test_dir}/sdlc-{task.id.lower()}.test")
                 for task in plan.tasks for n, criterion in enumerate(task.acceptance_criteria, start=1)]
        return TestPlan(cases=cases)

    def write_tests(self, context, task, tests) -> StepResult:
        count = sum(1 for case in tests.cases if case.task_id == task.id)
        return StepResult(True, f"mock: {count} test cases noted, no files written")

    def red_check(self, context, task) -> StepResult:
        return StepResult(True, "mock: red check skipped (no tests were written)")

    def implement(self, context, task, attempt) -> StepResult:
        return StepResult(True, f"mock: implementation skipped (attempt {attempt})")

    def validate(self, context, task) -> StepResult:
        names = ", ".join(command.name for command in context.policy.validation)
        return StepResult(True, f"mock: validation not run ({names})")

    def review(self, context, plan, tests) -> Review:
        coverage = {c.id: [case.id for case in tests.cases if case.criterion == c.id]
                    for c in parse_criteria(context.spec_md or "")}
        gaps = [criterion for criterion, cases in coverage.items() if not cases]
        rows = "\n".join(f"| {criterion} | {', '.join(cases) or 'none'} |" for criterion, cases in coverage.items())
        markdown = (f"# Review: {context.title}\n\nMock Code Reviewer: no diff was produced in B2.\n\n"
                    f"## Acceptance-criteria coverage\n\n| Criterion | Test cases |\n|---|---|\n{rows}\n\n"
                    f"## Findings\n\n" + ("\n".join(f"- Important: {g} has no test case" for g in gaps) or "_None._")
                    + "\n")
        return Review(verdict="concerns" if gaps else "pass", coverage=coverage, markdown=markdown,
                      findings=[{"severity": "important", "text": f"{g} has no test case"} for g in gaps])

    def open_pull_request(self, context, branch) -> ReleaseResult:
        return ReleaseResult(url=None, number=None, simulated=True)
