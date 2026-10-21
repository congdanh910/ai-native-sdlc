"""Deterministic stand-ins for every AI role, so the whole loop can be exercised in B2.

The mock reads the real intent, spec and repository file list, so its outputs
follow the request, but it calls no model and changes no code.
Write `[blocking]` before an open question in the intent to see the
clarification loop.
"""

import re

from controller.app.project_docs import Sds, Srs, srs_features, srs_flows, srs_state_entities
from controller.app.projects.config import matches_any
from controller.app.schemas import (Advice, Criterion, Intent, PlanPayload, PlanTask, Question, Review, Spec, TestCase,
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
        trace_notes = []
        if context.baseline:  # one feature per baseline feature, its functions as requirements; the rest in prose
            items = [i for i in context.baseline["items"] if i["type"] != "option"]
            used_ids: set[str] = set()
            features, used = [], set()
            for index, feature in enumerate([i for i in items if i["type"] == "feature"] or items[:1], start=1):
                prefix = f"F{chr(64 + index % 26 or 26)}"
                functions = [i for i in items if i["type"] == "function" and i["parent"] == feature["name"]]
                requirements = [{"function": f["name"][:40], "requirement": f"The system shall {f['text'] or f['name']}",
                                 "refs": [f["id"]]} for f in functions] or [
                    {"function": feature["name"][:40], "requirement": f"The system shall support {feature['name']}",
                     "refs": [feature["id"]]}]
                features.append({"name": feature["name"][:60], "prefix": prefix, "summary": feature["text"] or feature["name"],
                                 "requirements": requirements,
                                 "stories": [{"title": feature["name"][:60] if len(feature["name"]) >= 3 else f"Use {feature['name']}",
                                              "actors": [roles[0]["role"]], "goal": f"use {feature['name']}",
                                              "reason": "the baseline asks for it", "refs": [feature["id"]]}]})
                used_ids |= {feature["id"], *(f["id"] for f in functions)}
            trace_notes = [{"req": i["id"], "section": "1.2 Scope", "note": "mock"} for i in items if i["id"] not in used_ids]
        return Srs.model_validate({
            "product": title, "purpose": _section(brief, "Summary") or title, "trace_notes": trace_notes,
            "scope": "; ".join(f["name"] for f in features), "roles": roles,
            "assumptions": [f"Answer to '{q}': {a}" for q, a in context.answers],
            "entities": [{"name": f["name"].split()[0].capitalize(), "description": f["summary"]} for f in features],
            "features": features,
            "flows": [{"name": "Main flow", "steps": [f"{roles[0]['role']} uses {f['name']}" for f in features]
                       + ["The system records the result"]}],
            "questions": questions, "concerns": ["Mock Business Analyst: no model was called."]})

    def design_options(self, context: RunContext):
        """The baseline's option items, or two generic options, and the first one recommended."""
        from controller.app.project_docs import Options
        named = [i for i in (context.baseline or {}).get("items", []) if i["type"] == "option"]
        options = [{"name": i["name"] if len(i["name"]) >= 2 else f"Option {i['name']}", "summary": i["text"] or i["name"],
                    "refs": [i["id"]]} for i in named]
        while len(options) < 2:
            options.append({"name": ["Single server", "Managed cloud"][len(options)], "summary": "Mock Architect option"})
        platforms = context.hosting_platforms or (context.policy.hosting_platforms if context.policy else ["aws", "on_premise"])
        if "on_premise" in platforms and len(platforms) > 1:  # one cloud and on-premise, as the prompt asks
            platforms = [next(p for p in platforms if p != "on_premise"), "on_premise"]
        platforms = platforms[:max(1, min(2, context.policy.max_hosting_variants if context.policy else 2))]
        for option in options:  # a proposed stack, as when the brief leaves it to the Architect
            option["stack"] = [{"layer": "backend", "technology": "Node.js 20", "why": "the repository's stack"},
                               {"layer": "database", "technology": "PostgreSQL 16", "why": "relational data"}]
            option["hosting"] = [{"platform": p, "name": f"Mock hosting on {p}",
                                  "services": [{"node": "Application", "service": f"{p} container"}]} for p in platforms]
            option["recommended_hosting"] = option["hosting"][0]["name"]
        return Options.model_validate({"context": f"Where {context.title} runs.", "options": options,
                                       "recommendation": options[0]["name"], "reason": "Mock Architect: the first option."})

    def write_onboarding(self, context: RunContext):
        """The repository's toolchain kept, the structure of the SDS repository layout as paths."""
        from controller.app.foundation import Onboarding
        return Onboarding.model_validate({
            "agents_md": f"# {context.title}\n\nMock Architect: the stack of SDS.md.\n\n## Commands\n- Test: npm test\n",
            "review_md": "- Business rules outside src/domain/.\n", "gitignore": "node_modules/\n",
            "validation_image": "node-20",
            "validation": [{"name": "unit-tests", "run": ["npm", "test"]}],
            "paths": {"allowed": ["src/**", "package.json"], "tests": ["test/**"],
                      "protected": ["AGENTS.md", "CLAUDE.md", "REVIEW.md"]},
            "notes": "Mock Architect: no model was called."})

    def write_sds(self, context: RunContext) -> Sds:
        """One object and table per SRS feature, one decision for the stack named in the brief."""
        features = srs_features(context.srs_md or "")
        stack = (_section(context.intent_md, "Technology and stack") or _section(context.intent_md, "Technology and stack (constraint)")
                 or (", ".join(f"{c['layer']}: {c['technology']}" for c in (context.chosen_option or {}).get("stack", []))) or "Not chosen")
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
                           "stories": [s.id for s in f.stories], "response": '{"items": []}'} for f in features],
            "decisions": ([{"title": "Architecture Option", "context": "The Solution Architect chose between the options.",
                            "decision": f"{context.chosen_option['id']} {context.chosen_option['name']}"
                                        + (f", hosted as {context.chosen_option['hosting']['id']} "
                                           f"{context.chosen_option['hosting']['name']}"
                                           if context.chosen_option.get("hosting") else "")
                                        + f": {context.chosen_option['reason']}"
                                        + (f" Adjustments: {context.chosen_option['adjustments']}"
                                           if context.chosen_option.get("adjustments") else "")}]
                          if context.chosen_option else [])
                         + [{"title": "Technology Stack Selection", "context": "The brief names the stack.",
                             "decision": stack.splitlines()[0]}],
            # Enough of every part for the depth check: diagrams of the context, data, flows, lifecycles and sign-in.
            "actors": ["User"], "external_systems": [{"name": "Identity provider", "purpose": "sign-in"}],
            "states": [{"object": name, "states": [{"term": "Active", "meaning": "in use"}, {"term": "Inactive", "meaning": "retired"}],
                        "transitions": [{"source": "Active", "target": "Inactive", "trigger": "retire"}]}
                       for name in srs_state_entities(context.srs_md or "")],
            "tables": [{"name": f.prefix.lower(), "fields": [{"name": "id", "type": "uuid", "required": True}]} for f in features]
                      or [{"name": "item", "fields": [{"name": "id", "type": "uuid", "required": True}]}],
            "process": [{"name": flow, "participants": ["User", "Application"],
                         "messages": [{"source": "User", "target": "Application", "text": "request"}]}
                        for flow in srs_flows(context.srs_md or "")],
            "dtos": [{"name": f"{f.prefix}Response", "object": f.name, "fields": [{"name": "id", "type": "string"}]}
                     for f in features] or [{"name": "ItemResponse", "fields": [{"name": "id", "type": "string"}]}],
            "zones": [{"name": "Production network", "kind": "network"}],
            "deployment": [{"name": "Application server", "runs": "the application", "zone": "Production network",
                            "environment": "production"}],
            "links": [{"source": "User", "target": "Application server", "protocol": "HTTPS 443", "security": "TLS"}],
            "auth": {"method": "Session cookie", "flow": [{"source": "User", "target": "Application", "text": "sign in"}]},
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

    def write_acceptance_tests(self, context, criteria, story, feedback="") -> StepResult:
        return StepResult(True, "mock: acceptance tests noted, no files written")

    def red_check(self, context, task, tests) -> StepResult:
        return StepResult(True, "mock: red check skipped (no tests were written)")

    def implement(self, context, task, attempt, tests=None, feedback="") -> StepResult:
        return StepResult(True, f"mock: implementation skipped (attempt {attempt})")

    def validate(self, context, task, attempt=1) -> StepResult:
        names = ", ".join(command.name for command in context.policy.validation)
        return StepResult(True, f"mock: validation not run ({names})")

    def advise(self, context, stop) -> Advice:
        decision = next(iter(stop.get("decisions") or {"resume": ""}))
        return Advice(situation=f"Mock advice: {stop.get('stop', '')[:200]}", recommendation=decision, confidence="low",
                      reason=f"mock: {decision}", sides=[{"party": "the platform", "claim": "no model was asked"}])

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

    def extract_part(self, context: RunContext, part):
        """Each heading of a text part is a feature and each bullet under it a function, cited at the nearest
        bracketed location; a file part (PDF, image) gives one goal on its first page. No model is called."""
        from controller.app.discovery.inventory import ChunkResult, Citation, Gap, Item
        if part.file:
            first = part.pages.split("-")[0] if part.pages else ""
            location = f"page {first}" if first else part.label
            items = [Item(type="goal", name=f"Goals of {part.filename}", text="Mock Business Analyst: no model read the file.",
                          sources=[Citation(source=part.source_id, location=location)])]
            return ChunkResult(items=items, read=[part.label])
        items, gaps, feature, location = [], [], "", part.label
        for line in part.text.splitlines():
            marker = re.match(r"\s*\[([^\]]+)\]", line)
            if marker:
                location = marker[1]
            text = re.sub(r"^\s*\[[^\]]+\]\s*", "", line).strip()
            cite = [Citation(source=part.source_id, location=location)]
            if text.startswith("#"):
                feature = text.lstrip("# ").strip()
                if feature:
                    items.append(Item(type="feature", name=feature, sources=cite))
            elif text.startswith(("- ", "* ")):
                body = text[2:].strip()
                items.append(Item(type="function", name=body[:80], text=body, parent=feature, sources=cite))
                if "?" in body:
                    later = "[later]" in body  # a detail question the SRS settles
                    gaps.append(Gap(kind="scope", question=body.replace("[later]", "").strip(), items=[body[:80]],
                                    sources=cite, stage="later" if later else "baseline"))
        return ChunkResult(items=items, gaps=gaps, read=[part.label])

    def consolidate(self, context: RunContext, items: list[dict], gaps: list[dict], decisions=(), asked=(), round_info=None):
        """Each decision confirms the items it names; a decision that says "out of scope" takes them out."""
        from controller.app.discovery.inventory import Consolidation, Update
        updates = [Update(id=item, decision=d["id"], status="out_of_scope" if "out of scope" in d["text"].lower() else "confirmed")
                   for d in decisions for item in d.get("items", [])]
        suggestions = [{"question": a["id"], "suggestion": "Mock answer for the Product Owner to accept. (mock)"}
                       for a in asked if isinstance(a, dict) and a.get("needs_suggestion")]
        return Consolidation(updates=updates, suggestions=suggestions)

    def read_minutes(self, context: RunContext, part, questions: list[dict], items: list[dict]):
        """A line "Decision: ..." is a decision; "(Q-001)" in it answers that question, REQ ids in it are its items."""
        from controller.app.discovery.inventory import MinutesDecision, MinutesResult
        out = []
        for line in part.text.splitlines():
            match = re.search(r"Decision:\s*(.+)", line)
            if match:
                text = match[1].strip()
                question = re.search(r"\((Q-\d+)\)", text)
                out.append(MinutesDecision(text=re.sub(r"\s*\(Q-\d+\)", "", text), question=question[1] if question else "",
                                           items=re.findall(r"REQ-\d+", text), location=part.label))
        return MinutesResult(decisions=out, read=[part.label])
