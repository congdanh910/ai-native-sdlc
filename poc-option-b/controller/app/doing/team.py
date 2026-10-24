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
from controller.app.projects.config import matches_any, test_folders, unrun_tests
from controller.app.schemas import PlanTask, TestPlan, parse_criteria
from controller.app.team.base import RunContext, StepResult, SubscriptionLimit

PROMPTS = Path(__file__).with_name("prompts")
# read when the worker starts: a prompt edited while it runs must not meet code that does not fill its placeholders
TEMPLATES = {path.stem: path.read_text() for path in PROMPTS.glob("*.md")}


def _prompt(name: str, **values: str) -> str:
    return TEMPLATES[name].format(**values)


def _folders_rule(context: RunContext) -> str:
    folders = test_folders(context.policy.validation)
    if not folders:
        return "Put new tests where the project's test commands find them, next to the existing tests."
    return ("The validation runs tests only from: " + ", ".join(f"{path} ({name})" for path, name in folders.items())
            + ". A test file elsewhere in those parts of the code never runs: when a case's target file is outside "
              "them, write it in the folder of that list whose existing tests set up what it needs (the database).")


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

    def _preview(self, context: RunContext) -> str:
        """The preview's compose file as it is now: tests that start or check the running application use only its
        services (a later story's services are not there yet)."""
        preview = context.policy.preview
        compose = self._worktree(context) / preview.compose if preview else None
        if compose is None or not compose.is_file():
            return ""
        return (f"\n# The preview ({preview.compose})\n\nTests that start, check or call the running application use "
                f"only the services of this file, as it is now; do not expect services it does not have.\n\n"
                f"```yaml\n{compose.read_text()}\n```\n")

    @staticmethod
    def _criteria(context: RunContext, task: PlanTask) -> str:
        texts = {c.id: c.full for c in parse_criteria(context.spec_md or "")}
        return "\n".join(f"- {cid}: {texts.get(cid, '(not in the spec)')}" for cid in task.acceptance_criteria)

    @staticmethod
    def _cases(tests: TestPlan, task: PlanTask) -> list[dict]:
        return [case.model_dump() for case in tests.cases if case.task_id == task.id]

    def _session(self, executor: str, role: str, prompt: str, context: RunContext, task: PlanTask, writable: list[str],
                 label: str) -> SessionResult:
        """The role's executors in order (AI providers; only the project's executors): one out of quota is marked
        limited, what its session left uncommitted is dropped, and the next one works in the same job."""
        from controller.app import joblog, providers
        engines = [executor] if context.experiment else providers.chain(
            "doing", role, executor, allowed=list(context.policy.executors))
        free, back = providers.available([e for e in engines if e in self.executors])
        if not free:
            raise SubscriptionLimit(f"every executor of the {role} is out of quota ({', '.join(engines)})", until=back)
        message = ""
        for name in free:
            self.last_executor = name
            model, effort = ("", "") if context.experiment else providers.model_for(role, name)
            if model or effort:
                joblog.note(f"{role} on {name}: model {model or 'default'}" + (f", effort {effort}" if effort else ""))
            session = Session(role=role, prompt=prompt, worktree=self._worktree(context), writable=writable,
                              protected=list(context.policy.protected_paths), commands=self._commands(context, task),
                              timeout=context.policy.session_timeout_seconds,
                              log_path=self._log_dir(context) / f"{label}.log", model=model, effort=effort)
            result = self.executors[name].run(session)
            if not result.limited:
                return result
            message = f"{name} subscription limit reached during {label}: {result.message[-200:]}"
            until = providers.mark_limited(name, result.message)
            changes.discard_uncommitted(self._worktree(context))  # the next executor starts from the committed work
            joblog.note(f"{name} is out of quota until {until.astimezone():%H:%M}; the {role} moves to the next executor")
        _, back = providers.available([e for e in engines if e in self.executors])
        raise SubscriptionLimit(message, until=back)

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
            existing = changes.find_commit(worktree, context.task_base or context.base_commit, f"test({task.id}):")
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
                         edit_rule=edit_rule, preview=self._preview(context), test_folders=_folders_rule(context),
                         feedback=f"\n# {'Decision on the dispute' if amend else 'Previous attempt'}\n\n{feedback}\n"
                         if feedback else "")
        label = f"{task.id}-tests-{'amend' if amend else 2 if feedback else 1}"
        result = self._session(context.policy.qa_executor, "qa_engineer", prompt, context, task,
                               list(context.policy.test_paths), label)
        if amend and result.dispute:  # the decision cannot be followed as written: back to the Tech Lead
            changes.discard(worktree)
            return StepResult(False, result.dispute, dispute=True, evidence=self._evidence(result, label))
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
        # Under a ruling every touched existing test file is listed: inserting a line into an assertion changes it too.
        amended = [f"{c.path} (+{c.added}/-{c.deleted})" for c in staged if amend and c.status != "A"]
        subject = f"test({task.id}): {'update tests after a ruled dispute' if amend else task.title}"
        sha = changes.commit(worktree, subject, "qa_engineer")
        files = [c.path for c in staged]
        detail = f"{len(cases)} case(s) written in {', '.join(files)} ({result.executor})"
        if amended:
            detail += "; existing tests changed as decided (a ruling, or the defect notes): " + ", ".join(amended)
        return StepResult(True, detail, changed_files=files,
                          evidence=self._evidence(result, label, commit=sha, amended=amended))

    def write_acceptance_tests(self, context: RunContext, criteria: str, story: str, feedback: str = "") -> StepResult:
        """The QA Engineer's end-to-end tests of the story's criteria, for the preview; reused once committed."""
        worktree = self._worktree(context)
        changes.discard(worktree)
        preview = context.policy.preview
        existing = changes.find_commit(worktree, context.base_commit, "test(acceptance):") if context.base_commit else None
        # tests written for an earlier preview (the onboarding changed its compose file or project.yaml, which holds the
        # demo accounts, since) are written again
        stale = bool(existing) and bool({preview.compose, ".sdlc/project.yaml"} & set(changes.changed_since(worktree, existing)))
        if existing and not feedback and not stale:
            return StepResult(True, f"acceptance tests already committed in {existing[:10]}; reused",
                              evidence={"commit": existing, "reused": True})
        if stale and not feedback:
            feedback = (f"The preview or its demo accounts changed since these acceptance tests were written "
                        f"({preview.compose}, .sdlc/project.yaml); write them again for the preview and the accounts "
                        "as they are now (an account's note says which criterion it is for).")
        # the story's own acceptance suite is being rewritten (a new preview, a retest, errors in its code): it may
        # change or drop its files; still nothing outside the e2e paths, and no skipped tests
        rewrite = stale or bool(feedback)
        dropped = changes.drop_commit(worktree, "test(acceptance):") if feedback else False
        accounts = "\n".join(f"  - {a.role}: {a.username} / {a.password}" + (f" ({a.note})" if a.note else "")
                             for a in preview.accounts) or "  - none: ask in your answer"
        prompt = _prompt("acceptance-tests", agents_md=context.agents_md, story=story, criteria=criteria,
                         e2e_paths=", ".join(preview.e2e_paths), accounts=accounts, e2e_command=" ".join(preview.e2e),
                         story_tag=(context.story or {}).get("story_id") or "FOUNDATION",
                         preview=self._preview(context),
                         feedback=f"\n# Previous attempt\n\n{feedback}\n" if feedback else "")
        # not a task of the plan: built without its checks, and every validation command is available to the session
        task = PlanTask.model_construct(id="ACC", title="Acceptance tests", acceptance_criteria=["all"], validation=[])
        label = f"acceptance-tests-{2 if feedback else 1}"
        result = self._session(context.policy.qa_executor, "qa_engineer", prompt, context, task,
                               list(preview.e2e_paths), label)
        if not result.ok:
            changes.discard(worktree)
            return StepResult(False, f"QA Engineer session failed ({result.executor}): {result.message[-300:]}",
                              evidence=self._evidence(result, label))
        staged = changes.stage_changes(worktree)
        e2e_policy = context.policy.model_copy(update={"test_paths": list(preview.e2e_paths)})
        # the rewritten suite may change or drop its own files (see rewrite above)
        if not staged and existing and not dropped:
            # asked to look again (the QC's notes after fixed defects) and nothing needed changing: the suite stays
            return StepResult(True, f"acceptance tests in {existing[:10]} kept; the QA Engineer changed nothing "
                                    f"({result.executor}): {result.message[-300:]}",
                              evidence={**self._evidence(result, label), "commit": existing, "reused": True})
        problems = changes.check_tests(worktree, staged, e2e_policy, allow_edits=rewrite, allow_deletes=rewrite) if staged else ["no acceptance test was written"]
        if problems:
            changes.discard(worktree)
            return StepResult(False, "acceptance tests rejected: " + "; ".join(problems),
                              evidence=self._evidence(result, label, problems=problems))
        sha = changes.commit(worktree, "test(acceptance): end-to-end tests of the story's criteria", "qa_engineer")
        files = [c.path for c in staged]
        return StepResult(True, f"acceptance tests written in {', '.join(files)} ({result.executor})",
                          changed_files=files, evidence=self._evidence(result, label, commit=sha))

    def red_check(self, context: RunContext, task: PlanTask, tests: TestPlan) -> StepResult:
        if context.base_commit:
            earlier = changes.find_commit(self._worktree(context), context.task_base or context.base_commit,
                                          f"feat({task.id}):")
            if earlier:  # code is only committed after a passing red check, so these tests were red before it
                return StepResult(True, f"red check passed before the earlier implementation {earlier[:10]}",
                                  evidence={"reused": True})
        cases = self._cases(tests, task)
        unrun = unrun_tests(self._test_files(context, task), context.policy.validation)
        if unrun:  # the commands would pass without them: that is not "already pass", the QA Engineer moves them
            return StepResult(False, f"the new tests in {', '.join(unrun)} never run: no validation command runs that "
                                     f"folder. {_folders_rule(context)}", evidence={"unrun": unrun})
        expect_pass = bool(cases) and all(case["expected_pass"] for case in cases)
        # Every command, not only the task's: a task may list just a syntax check, and the new tests must run.
        names = [c.name for c in context.policy.validation]
        lint = frozenset(c.name for c in context.policy.validation if c.is_lint)
        ok, detail, reports = red_check(self._gate(context), names, expect_pass, f"{task.id}-red", lint)
        unclean = self._lint_in_tests(context, task, reports, lint)
        if unclean:  # the QA Engineer's own lines break the project's lint: back to it, before any code is written
            return StepResult(False, "the new tests fail the project's lint (fix the test files, not the rules): "
                                     + "; ".join(unclean), evidence={"reports": reports})
        runners = self._runners_this_task_creates(context, task, cases)
        if not ok and not expect_pass and runners:
            # The commands skip a part whose manifest is missing, so its new tests cannot fail before the task
            # creates it; the validation after the code runs them.
            return StepResult(True, f"red by construction: the tests need {', '.join(runners)}, which this task "
                                    "creates; the validation after the code runs them", evidence={"reports": reports})
        return StepResult(ok, detail, evidence={"reports": reports})

    def _test_files(self, context: RunContext, task: PlanTask) -> list[str]:
        """The test files of the task's committed tests (the QA Engineer may have put a case elsewhere than its
        target file)."""
        if not context.base_commit:
            return []
        worktree = self._worktree(context)
        commit = changes.find_commit(worktree, context.task_base or context.base_commit, f"test({task.id}):")
        return [f for f in changes.files_of(worktree, commit) if matches_any(f, context.policy.test_paths)] if commit else []

    def _lint_in_tests(self, context: RunContext, task: PlanTask, reports: list[dict], lint: frozenset[str]) -> list[str]:
        """Lint failures that name a test file the QA Engineer committed for this task, with the lines saying so."""
        worktree = self._worktree(context)
        commit = changes.find_commit(worktree, context.task_base or context.base_commit, f"test({task.id}):") \
            if context.base_commit else None
        files = changes.files_of(worktree, commit) if commit else []
        found = []
        for report in reports:
            if report["name"] in lint and not report["ok"]:
                text = Path(report["log_path"]).read_text() if Path(report["log_path"]).is_file() else report["tail"]
                lines = [line.strip() for line in text.splitlines() if any(f in line for f in files)]
                if lines:
                    found.append(f"{report['name']}: " + " | ".join(lines[:6]))
        return found

    def _runners_this_task_creates(self, context: RunContext, task: PlanTask, cases: list[dict]) -> list[str]:
        """The dependency manifests (environment.setup_inputs) this task creates that the new tests live under."""
        worktree = self._worktree(context)
        folders = {str(Path(c["target_file"]).parent) for c in cases if c.get("target_file")}
        return [m for m in context.policy.setup_inputs if m in task.create and not (worktree / m).exists()
                and "/" in m and any(f == str(Path(m).parent) or f.startswith(str(Path(m).parent) + "/")
                                     for f in folders)]

    def implement(self, context: RunContext, task: PlanTask, attempt: int, tests: TestPlan | None = None,
                  feedback: str = "") -> StepResult:
        worktree = self._worktree(context)
        changes.discard(worktree)
        cases = self._cases(tests, task) if tests else []
        references = ", ".join(f"{r.symbol} in {r.file}" if r.symbol else r.file for r in task.references) or "none"
        prompt = _prompt("developer", agents_md=context.agents_md, task_id=task.id, task_title=task.title,
                         task_files=", ".join(task.modify + task.create), references=references,
                         provides=", ".join(task.provides) or "none", criteria=self._criteria(context, task),
                         test_files=", ".join(self._test_files(context, task)
                                              or sorted({c["target_file"] for c in cases})) or "the test paths",
                         cases=json.dumps(cases, indent=2),
                         commands="; ".join(" ".join(c) for c in self._commands(context, task)),
                         lock_note=(f"When you change a dependency manifest, do not write its lockfile: the platform runs "
                                    f"`{' '.join(context.policy.lock)}` with network after your change and commits the "
                                    "lockfiles." if context.policy.lock else
                                    "Do not compose a lockfile by hand from other projects; leave it to setup."),
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
        earlier = changes.find_commit(worktree, context.task_base or context.base_commit, f"feat({task.id}):") \
            if not staged and context.base_commit else None
        if earlier:  # resumed after a failure outside the code: the earlier implementation stands, validation decides
            return StepResult(True, f"no change: the implementation committed in {earlier[:10]} stays "
                                    f"({result.executor}, attempt {attempt})", evidence=self._evidence(result, label))
        # tests are the QA Engineer's: test files the Developer wrote anyway (AGENTS.md often asks for tests with every
        # change) are dropped, the code stays and the QA Engineer's tests decide; changing them is a dispute
        own_tests = [c.path for c in staged if c.status == "A" and matches_any(c.path, context.policy.test_paths)]
        if own_tests:
            staged = changes.drop_paths(worktree, staged, own_tests)
        problems = changes.check_code(worktree, staged, task, context.policy)
        if problems:
            changes.discard(worktree)
            return StepResult(False, "code changes rejected: " + "; ".join(problems),
                              evidence=self._evidence(result, label, problems=problems))
        sha = changes.commit(worktree, f"feat({task.id}): {task.title}" + (f" (attempt {attempt})" if attempt > 1 else ""),
                             "developer")
        files = [c.path for c in staged]
        dropped = f"; dropped the Developer's own test files {', '.join(own_tests)}" if own_tests else ""
        return StepResult(True, f"changed {', '.join(files)} ({result.executor}, attempt {attempt}){dropped}",
                          changed_files=files, evidence=self._evidence(result, label, commit=sha, dropped=own_tests))

    def lock(self, context: RunContext, task: PlanTask) -> StepResult | None:
        """After the task changed a dependency manifest, the project's lock command writes the lockfiles (with
        network, which the Developer's session has not) and the platform commits them; None when nothing to do."""
        policy, worktree = context.policy, self._worktree(context)
        base = context.task_base or context.base_commit
        if not policy.lock or not base or not any(matches_any(p, policy.setup_inputs)
                                                  for p in changes.changed_since(worktree, base)):
            return None
        gate = self._gate(context)
        marker = self._log_dir(context) / "lock.done"
        if marker.exists() and marker.read_text().strip() == gate._setup_key(list(policy.lock)):
            return None
        report = gate.lock()
        if not report.ok:
            changes.discard(worktree)
            return StepResult(False, f"the lock command failed (exit {report.exit_code}): {report.tail[-600:]}",
                              evidence={"reports": [asdict(report)], "failures": report.tail[-6000:]})
        staged = changes.stage_changes(worktree)
        other = [c.path for c in staged if not matches_any(c.path, policy.setup_inputs)]
        if other:
            changes.discard(worktree)
            return StepResult(False, "the lock command changed files that are not setup inputs: " + ", ".join(other),
                              evidence={"reports": [asdict(report)]})
        if staged:
            changes.commit(worktree, f"build({task.id}): lock dependencies", "validation_gate")
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(gate._setup_key(list(policy.lock)) + "\n")
        return StepResult(True, "locked: " + (", ".join(c.path for c in staged) or "lockfiles already current"),
                          changed_files=[c.path for c in staged])

    def validate(self, context: RunContext, task: PlanTask, attempt: int = 1) -> StepResult:
        locked = self.lock(context, task)
        if locked and not locked.ok:  # the Developer's remediation gets the lock command's error
            return locked
        reports = self._gate(context).run([c.name for c in context.policy.validation], f"{task.id}-validate-{attempt}")
        ok = bool(reports) and all(r.ok for r in reports)
        return StepResult(ok, summary(reports), evidence={"reports": [asdict(r) for r in reports],
                                                          "failures": "" if ok else failures(reports)})
