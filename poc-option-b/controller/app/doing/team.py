"""The doing roles for real: QA Engineer and Developer on Codex/Claude CLI, Validation Gate in a container.

Routing is deterministic: the QA Engineer uses the project's qa_executor and the
Developer its default_executor (`.sdlc/project.yaml`). Every step leaves evidence
(session log, usage, commit, validation reports) on the step result.
"""

from dataclasses import asdict
import json
from pathlib import Path

from controller.app.config import DATA_DIR
from controller.app.doing import changes
from controller.app.doing.executors import Session, SessionResult, executors as default_executors
from controller.app.doing.validation import ValidationGate, failures, red_check, summary
from controller.app.schemas import PlanTask, TestPlan, parse_criteria
from controller.app.team.base import RunContext, StepResult

PROMPTS = Path(__file__).with_name("prompts")
SESSION_TIMEOUT = 1200


def _prompt(name: str, **values: str) -> str:
    return (PROMPTS / f"{name}.md").read_text().format(**values)


class CliDoing:
    def __init__(self, executors: dict | None = None, gate_factory=ValidationGate, log_root: Path = DATA_DIR / "logs"):
        self.executors = executors or default_executors()
        self.gate_factory, self.log_root = gate_factory, log_root
        self.last_executor: str | None = None

    # ------------------------------------------------------------ helpers

    def _worktree(self, context: RunContext) -> Path:
        if not context.worktree:
            raise ValueError("the run has no worktree")
        return Path(context.worktree)

    def _log_dir(self, context: RunContext) -> Path:
        return self.log_root / context.run_id

    def _gate(self, context: RunContext) -> ValidationGate:
        return self.gate_factory(context.policy, self._worktree(context), self._log_dir(context))

    def _commands(self, context: RunContext, task: PlanTask) -> list[list[str]]:
        return [list(c.run) for c in context.policy.validation if c.name in task.validation] or \
               [list(c.run) for c in context.policy.validation]

    @staticmethod
    def _criteria(context: RunContext, task: PlanTask) -> str:
        texts = {c.id: c.text for c in parse_criteria(context.spec_md or "")}
        return "\n".join(f"- {cid}: {texts.get(cid, '(not in the spec)')}" for cid in task.acceptance_criteria)

    @staticmethod
    def _cases(tests: TestPlan, task: PlanTask) -> list[dict]:
        return [case.model_dump() for case in tests.cases if case.task_id == task.id]

    def _session(self, executor: str, role: str, prompt: str, context: RunContext, task: PlanTask, writable: list[str],
                 label: str) -> SessionResult:
        self.last_executor = executor
        session = Session(role=role, prompt=prompt, worktree=self._worktree(context), writable=writable,
                          protected=list(context.policy.protected_paths), commands=self._commands(context, task),
                          timeout=SESSION_TIMEOUT, log_path=self._log_dir(context) / f"{label}.log")
        return self.executors[executor].run(session)

    @staticmethod
    def _evidence(result: SessionResult, label: str, **extra) -> dict:
        return {"executor": result.executor, "seconds": result.seconds, "exit_code": result.exit_code,
                "timed_out": result.timed_out, "usage": result.usage, "log": label, "message": result.message[-2000:],
                **extra}

    # ------------------------------------------------------------ roles

    def write_tests(self, context: RunContext, task: PlanTask, tests: TestPlan, feedback: str = "",
                    amend: bool = False) -> StepResult:
        worktree = self._worktree(context)
        changes.discard(worktree)  # a crashed earlier session must not leak into this one
        if not feedback and not amend and context.base_commit:
            existing = changes.find_commit(worktree, context.base_commit, f"test({task.id}):")
            if existing:  # a resumed task keeps the tests it already has
                return StepResult(True, f"tests already committed in {existing[:10]}; reused",
                                  evidence={"commit": existing, "reused": True})
        if feedback and not amend:  # the red check rejected the previous tests: rewrite, do not add a second copy
            changes.drop_commit(worktree, f"test({task.id}):")
        cases = self._cases(tests, task)
        edit_rule = ("A human decided that existing tests must change for this task (see below). You may edit "
                     "existing test lines where the decision requires it; never delete test files, and never skip "
                     "or focus tests (no .skip, .only, todo)." if amend else
                     "Add new test files or add new tests to existing files. Never change, remove, skip or focus "
                     "existing tests (no .skip, .only, todo).")
        prompt = _prompt("qa-engineer", agents_md=context.agents_md, task_id=task.id, task_title=task.title,
                         task_files=", ".join(task.modify + task.create), criteria=self._criteria(context, task),
                         cases=json.dumps(cases, indent=2), test_paths=", ".join(context.policy.test_paths),
                         commands="; ".join(" ".join(c) for c in self._commands(context, task)),
                         edit_rule=edit_rule,
                         feedback=f"\n# {'Decision on the dispute' if amend else 'Previous attempt'}\n\n{feedback}\n"
                         if feedback else "")
        label = f"{task.id}-tests-{'amend' if amend else 2 if feedback else 1}"
        result = self._session(context.policy.qa_executor, "qa_engineer", prompt, context, task,
                               list(context.policy.test_paths), label)
        if not result.ok:
            changes.discard(worktree)
            return StepResult(False, f"QA Engineer session failed ({result.executor}): {result.message[-300:]}",
                              evidence=self._evidence(result, label))
        staged = changes.stage_changes(worktree)
        problems = changes.check_tests(worktree, staged, context.policy, allow_edits=amend)
        if problems:
            changes.discard(worktree)
            return StepResult(False, "test changes rejected: " + "; ".join(problems),
                              evidence=self._evidence(result, label, problems=problems))
        amended = [f"{c.path} (-{c.deleted} line(s))" for c in staged if c.deleted]
        subject = f"test({task.id}): {'update tests after a ruled dispute' if amend else task.title}"
        sha = changes.commit(worktree, subject, "qa_engineer")
        files = [c.path for c in staged]
        detail = f"{len(cases)} case(s) written in {', '.join(files)} ({result.executor})"
        if amended:
            detail += "; existing tests changed by human decision: " + ", ".join(amended)
        return StepResult(True, detail, changed_files=files,
                          evidence=self._evidence(result, label, commit=sha, amended=amended))

    def red_check(self, context: RunContext, task: PlanTask, tests: TestPlan) -> StepResult:
        if context.base_commit:
            earlier = changes.find_commit(self._worktree(context), context.base_commit, f"feat({task.id}):")
            if earlier:  # code is only committed after a passing red check, so these tests were red before it
                return StepResult(True, f"red check passed before the earlier implementation {earlier[:10]}",
                                  evidence={"reused": True})
        cases = self._cases(tests, task)
        expect_pass = bool(cases) and all(case["expected_pass"] for case in cases)
        ok, detail, reports = red_check(self._gate(context), task.validation, expect_pass, f"{task.id}-red")
        return StepResult(ok, detail, evidence={"reports": reports})

    def implement(self, context: RunContext, task: PlanTask, attempt: int, tests: TestPlan | None = None,
                  feedback: str = "") -> StepResult:
        worktree = self._worktree(context)
        changes.discard(worktree)
        cases = self._cases(tests, task) if tests else []
        references = ", ".join(f"{r.symbol} in {r.file}" if r.symbol else r.file for r in task.references) or "none"
        prompt = _prompt("developer", agents_md=context.agents_md, task_id=task.id, task_title=task.title,
                         task_files=", ".join(task.modify + task.create), references=references,
                         provides=", ".join(task.provides) or "none", criteria=self._criteria(context, task),
                         test_files=", ".join(sorted({c["target_file"] for c in cases})) or "the test paths",
                         cases=json.dumps(cases, indent=2),
                         commands="; ".join(" ".join(c) for c in self._commands(context, task)),
                         feedback="" if not feedback else
                         f"\n# Note from the Tech Lead\n\n{feedback}\n" if attempt == 1 else
                         (f"\n# Validation failed after your previous attempt ({attempt - 1})\n\n"
                          f"Fix the code, not the tests.\n\n{feedback}\n"))
        label = f"{task.id}-code-{attempt}"
        result = self._session(context.policy.default_executor, "developer", prompt, context, task,
                               task.modify + task.create, label)
        if result.dispute:
            changes.discard(worktree)
            return StepResult(False, result.dispute, dispute=True, evidence=self._evidence(result, label))
        if not result.ok:
            changes.discard(worktree)
            return StepResult(False, f"Developer session failed ({result.executor}): {result.message[-300:]}",
                              evidence=self._evidence(result, label))
        staged = changes.stage_changes(worktree)
        problems = changes.check_code(worktree, staged, task, context.policy)
        if problems:
            changes.discard(worktree)
            return StepResult(False, "code changes rejected: " + "; ".join(problems),
                              evidence=self._evidence(result, label, problems=problems))
        sha = changes.commit(worktree, f"feat({task.id}): {task.title}" + (f" (attempt {attempt})" if attempt > 1 else ""),
                             "developer")
        files = [c.path for c in staged]
        return StepResult(True, f"changed {', '.join(files)} ({result.executor}, attempt {attempt})",
                          changed_files=files, evidence=self._evidence(result, label, commit=sha))

    def validate(self, context: RunContext, task: PlanTask, attempt: int = 1) -> StepResult:
        reports = self._gate(context).run([c.name for c in context.policy.validation], f"{task.id}-validate-{attempt}")
        ok = bool(reports) and all(r.ok for r in reports)
        return StepResult(ok, summary(reports), evidence={"reports": [asdict(r) for r in reports],
                                                          "failures": "" if ok else failures(reports)[-6000:]})
