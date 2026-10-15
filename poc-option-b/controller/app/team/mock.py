"""Deterministic stand-ins for every AI role, so the whole loop can be exercised in B2.

The mock reads the real intent, spec and repository file list, so its outputs
follow the request, but it calls no model and changes no code.
Write `[blocking]` before an open question in the intent to see the
clarification loop.
"""

import re

from controller.app.project_docs import Sds, Srs, srs_features
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
        criteria = [Criterion(id=f"AC-{i:02d}", text=" ".join(text.split()[:4]).rstrip(".,"), given="the product as it is",
                              when="the user uses it", then=text) for i, text in enumerate(outcome, start=1)]
        assumptions = [f"Answer to '{q}': {a}" for q, a in context.answers]
        return Spec(goal=_section(context.intent_md, "Problem") or context.title,
                    user_stories=[f"**As a** user, **I want to** {text[0].lower() + text[1:]}" for text in outcome],
                    acceptance_criteria=criteria, assumptions=assumptions,
                    concerns=["Mock Business Analyst: no model was called."], questions=questions)

    def write_srs(self, context: RunContext) -> Srs:
        """One feature per wanted feature line, one story each, every story for the first role."""
        brief = context.intent_md
        title = re.sub(r"^# Project brief:\s*", "", brief.splitlines()[0]) if brief else context.title
        roles = []
        for line in _items(_section(brief, "Users and roles")) or ["USER: a person using the product"]:
            name, _, description = line.partition(":")
            code = re.sub(r"[^A-Z0-9]+", "_", name.strip().upper()).strip("_")[:16] or "USER"
            if len(code) < 2:
                code = f"{code}X"
            if code not in {r["role"] for r in roles}:
                roles.append({"role": code, "description": description.strip() or name.strip()})
        answered = {question for question, _ in context.answers}
        questions = [{"question": line[len("[blocking]"):].strip(), "blocking": True}
                     for line in _items(_section(brief, "Constraints")) if line.lower().startswith("[blocking]")
                     and line[len("[blocking]"):].strip() not in answered]
        features, used = [], set()
        for line in _items(_section(brief, "Wanted features")) or [title]:
            words = re.findall(r"[A-Za-z]+", line) or ["Feature"]
            prefix = "".join(w[0] for w in words[:3]).upper().ljust(2, "X")
            while prefix in used:
                prefix = (prefix[:3] + chr(ord("A") + len(used) % 26))[:4]
            used.add(prefix)
            features.append({"name": line[:60], "prefix": prefix, "summary": line,
                             "requirements": [{"function": line[:40], "requirement": f"The system shall support: {line}"}],
                             "stories": [{"title": line[:60], "actors": [roles[0]["role"]], "goal": line[0].lower() + line[1:],
                                          "reason": "the brief asks for it"}]})
        return Srs.model_validate({
            "product": title, "purpose": _section(brief, "Summary") or title,
            "scope": "; ".join(f["name"] for f in features), "roles": roles,
            "assumptions": [f"Answer to '{q}': {a}" for q, a in context.answers],
            "entities": [{"name": f["name"].split()[0].capitalize(), "description": f["summary"]} for f in features],
            "features": features,
            "flows": [{"name": "Main flow", "steps": [f"{roles[0]['role']} uses {f['name']}" for f in features]
                       + ["The system records the result"]}],
            "questions": questions, "concerns": ["Mock Business Analyst: no model was called."]})

    def write_sds(self, context: RunContext) -> Sds:
        """One object and table per SRS feature, one decision for the stack named in the brief."""
        features = srs_features(context.srs_md or "")
        stack = _section(context.intent_md, "Technology and stack") or "Not chosen"
        answered = {question for question, _ in context.answers}
        questions = [{"question": line[len("[blocking]"):].strip(), "blocking": True}
                     for line in _items(_section(context.intent_md, "Notes")) if line.lower().startswith("[blocking]")
                     and line[len("[blocking]"):].strip() not in answered]
        return Sds.model_validate({
            "purpose": f"Design for {context.title}.", "scope": ", ".join(f.name for f in features) or "Whole product",
            "assumptions": [f"Answer to '{q}': {a}" for q, a in context.answers],
            "traceability": [{"entity": "N/A", "object": f.name.replace(" ", ""), "table": f.prefix.lower()}
                             for f in features] or [{"object": "Product"}],
            "context": "Users reach the product through a web browser.",
            "components": [{"name": f"{f.name} module", "responsibility": f"Implements {f.prefix} stories"} for f in features]
                          or [{"name": "Application", "responsibility": "Everything"}],
            "stack": [{"term": "Application", "meaning": stack.splitlines()[0]}],
            "purposes": {s.id: f"Let {s.actors} {s.title.lower()}." for f in features for s in f.stories},
            "endpoints": [{"method": "GET", "path": f"/api/v1/{f.prefix.lower()}", "feature": f.prefix,
                           "stories": [s.id for s in f.stories]} for f in features],
            "decisions": [{"title": "Technology Stack Selection", "context": "The brief names the stack.",
                           "decision": stack.splitlines()[0]}],
            "questions": questions, "concerns": ["Mock Architect: no model was called."]})

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

    def write_tests(self, context, task, tests, feedback="", amend=False) -> StepResult:
        count = sum(1 for case in tests.cases if case.task_id == task.id)
        return StepResult(True, f"mock: {count} test cases noted, no files written")

    def red_check(self, context, task, tests) -> StepResult:
        return StepResult(True, "mock: red check skipped (no tests were written)")

    def implement(self, context, task, attempt, tests=None, feedback="") -> StepResult:
        return StepResult(True, f"mock: implementation skipped (attempt {attempt})")

    def validate(self, context, task, attempt=1) -> StepResult:
        names = ", ".join(command.name for command in context.policy.validation)
        return StepResult(True, f"mock: validation not run ({names})")

    def review(self, context, plan, tests, diff="", validation="") -> Review:
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
