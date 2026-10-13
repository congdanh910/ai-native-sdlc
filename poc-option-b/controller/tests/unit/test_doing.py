import json
from pathlib import Path
import shutil
import subprocess

import pytest

from controller.app.doing import changes
from controller.app.doing.executors import ClaudeExecutor, CodexExecutor, Session, SessionResult
from controller.app.doing.guard import decide
from controller.app.doing.process import ProcessResult, redact, run_bounded
from controller.app.doing.team import CliDoing
from controller.app.doing.validation import ValidationGate, red_check
from controller.app.projects.config import effective_policy, parse_project_config
from controller.app.config import PROJECT_ROOT
from controller.app.schemas import PlanTask, TestPlan
from controller.app.team.base import RunContext
from controller.tests.conftest import git


def policy(**overrides):
    text = (PROJECT_ROOT / "examples/task-board/.sdlc/project.yaml").read_text()
    return effective_policy(parse_project_config(text)).model_copy(update=overrides)  # src/**, tests test/**


TASK = PlanTask(id="T1", title="Add due date", modify=["src/tasks.js"], acceptance_criteria=["AC-1"],
                validation=["unit-tests"])
TESTS = TestPlan(cases=[{"id": "TC-1", "task_id": "T1", "criterion": "AC-1", "description": "d", "expected": "e",
                         "target_file": "test/tasks.test.js"}])


def context(worktree: Path, **overrides) -> RunContext:
    overrides.setdefault("setup", [])
    return RunContext(title="Due dates", policy=policy(**overrides), agents_md="Keep rules in src/tasks.js.",
                      review_md=None, intent_md="# Intent", files=[], spec_md="- AC-1: tasks have a due date",
                      run_id="run-1", worktree=str(worktree))


# ------------------------------------------------------------------ process, guard

def test_bounded_process_kills_the_group_on_timeout():
    result = run_bounded(["sh", "-c", "sleep 30 & sleep 30"], timeout=0.5)
    assert result.timed_out and result.exit_code is None and not result.ok and result.seconds < 10


def test_output_is_redacted():
    assert redact("token=abc123 ghp_" + "a" * 36) == "[redacted] [redacted]"
    assert run_bounded(["echo", "password: hunter2"], timeout=5).output.strip() == "[redacted]"


def test_guard_blocks_paths_outside_the_role(tmp_path):
    def reason(path):
        return decide({"tool_input": {"file_path": path}, "cwd": str(tmp_path)}, tmp_path, ["test/**"], [".env"])
    assert reason("test/tasks.test.js") is None
    assert "not one of the files" in reason("src/tasks.js")
    assert "outside the repository" in reason("/etc/passwd")
    assert "outside the repository" in reason("../escape.js")
    assert decide({"tool_input": {"pattern": "*.js"}}, tmp_path, [], []) is None  # not a file edit


def test_guard_script_exit_code_blocks(tmp_path):
    event = json.dumps({"tool_input": {"file_path": str(tmp_path / "src/a.js")}, "cwd": str(tmp_path)})
    env = {"SDLC_GUARD_ROOT": str(tmp_path), "SDLC_GUARD_ALLOW": '["test/**"]', "PATH": "/usr/bin:/bin"}
    guard = PROJECT_ROOT / "controller/app/doing/guard.py"
    result = subprocess.run(["python3", "-I", str(guard)], input=event, capture_output=True, text=True, env=env)
    assert result.returncode == 2 and "Blocked by the SDLC controller" in result.stderr


# ------------------------------------------------------------------ change checks

@pytest.fixture
def worktree(sample_repo) -> Path:
    return sample_repo


def test_qa_may_only_add_tests(worktree):
    (worktree / "test" / "tasks.test.js").write_text("// existing tests\ntest('due', () => {});\n")
    (worktree / "test" / "dates.test.js").write_text("test('x', () => {});\n")
    assert changes.check_tests(worktree, changes.stage_changes(worktree), policy()) == []

    changes.discard(worktree)
    (worktree / "test" / "tasks.test.js").write_text("test.skip('due', () => {});\n")
    (worktree / "src" / "tasks.js").write_text("// stub\n")
    problems = "\n".join(changes.check_tests(worktree, changes.stage_changes(worktree), policy()))
    assert "changes src/tasks.js, outside the test paths" in problems
    assert "removes 1 existing line(s) in test/tasks.test.js" in problems
    assert "skipped or focused test" in problems
    changes.discard(worktree)
    assert git(worktree, "status", "--porcelain") == ""


def test_dependencies_are_neither_staged_nor_cleaned(worktree):
    (worktree / "node_modules" / "pkg").mkdir(parents=True)
    (worktree / "node_modules" / "pkg" / "index.js").write_text("x")
    (worktree / "test" / "new.test.js").write_text("test('x', () => {});\n")
    assert [c.path for c in changes.stage_changes(worktree)] == ["test/new.test.js"]
    changes.discard(worktree)
    assert (worktree / "node_modules" / "pkg" / "index.js").exists() and not (worktree / "test" / "new.test.js").exists()


def test_developer_may_only_change_the_task_files(worktree):
    (worktree / "src" / "tasks.js").write_text("export const x = 1;\n")
    assert changes.check_code(worktree, changes.stage_changes(worktree), TASK, policy()) == []
    changes.discard(worktree)
    (worktree / "src" / "app.js").write_text("// other\n")
    (worktree / "test" / "tasks.test.js").write_text("// weakened\n")
    problems = "\n".join(changes.check_code(worktree, changes.stage_changes(worktree), TASK, policy()))
    assert "src/app.js, which is not in task T1" in problems and "edits test file test/tasks.test.js" in problems


# ------------------------------------------------------------------ CLI doing roles with fakes

class FakeExecutor:
    """Edits files the way a CLI session would, without calling a model."""

    def __init__(self, name, edits=None, message="done", ok=True):
        self.name, self.edits, self.message, self.ok, self.sessions = name, edits or {}, message, ok, []

    def run(self, session: Session) -> SessionResult:
        self.sessions.append(session)
        for path, text in self.edits.items():
            (session.worktree / path).write_text(text)
        return SessionResult(self.name, self.ok, self.message, 1.0, 0 if self.ok else 1, False, {"turns": 3})


class FakeRunner:
    def __init__(self, exit_codes):
        self.exit_codes, self.calls = list(exit_codes), []

    def __call__(self, image, worktree, argv, timeout, network=False, writable=False):
        self.calls.append((image, argv, network, writable))
        code = self.exit_codes.pop(0)
        return ProcessResult(["docker", *argv], code, f"exit {code}", 0.1)


def doing(tmp_path, qa=None, dev=None, exit_codes=()):
    runner = FakeRunner(exit_codes)
    return CliDoing({"codex": dev or FakeExecutor("codex"), "claude": qa or FakeExecutor("claude")},
                    gate_factory=lambda p, w, l: ValidationGate(p, w, l, runner=runner), log_root=tmp_path / "logs"), runner


def test_qa_engineer_commits_tests_and_routes_to_the_qa_executor(worktree, tmp_path):
    qa = FakeExecutor("claude", {"test/due.test.js": "test('due', () => {});\n"})
    team, _ = doing(tmp_path, qa=qa)
    result = team.write_tests(context(worktree, qa_executor="claude"), TASK, TESTS)
    assert result.ok and result.changed_files == ["test/due.test.js"] and result.evidence["executor"] == "claude"
    assert "SDLC-Role: qa_engineer" in git(worktree, "log", "-1", "--format=%B")
    session = qa.sessions[0]
    assert session.writable == ["test/**"] and "TC-1" in session.prompt and "Keep rules in src/tasks.js." in session.prompt


def test_rejected_test_changes_are_discarded(worktree, tmp_path):
    team, _ = doing(tmp_path, qa=FakeExecutor("claude", {"src/tasks.js": "// stub to make tests pass\n"}))
    result = team.write_tests(context(worktree, qa_executor="claude"), TASK, TESTS)
    assert not result.ok and "outside the test paths" in result.detail
    assert git(worktree, "status", "--porcelain") == "" and git(worktree, "rev-list", "--count", "HEAD") == "1\n"


def test_developer_dispute_and_remediation_feedback(worktree, tmp_path):
    team, _ = doing(tmp_path, dev=FakeExecutor("codex", message="DISPUTE: TC-1 expects a format AC-1 does not state"))
    result = team.implement(context(worktree), TASK, 1, TESTS)
    assert result.dispute and "TC-1 expects" in result.detail

    dev = FakeExecutor("codex", {"src/tasks.js": "export function addTask() {}\n"})
    team, _ = doing(tmp_path, dev=dev)
    result = team.implement(context(worktree), TASK, 2, TESTS, feedback="AssertionError: expected 1")
    assert result.ok and result.evidence["commit"] == changes.head(worktree)
    assert "AssertionError: expected 1" in dev.sessions[0].prompt and dev.sessions[0].writable == ["src/tasks.js"]


def test_red_check_needs_a_failing_test_unless_cases_are_regression_guards(worktree, tmp_path):
    team, runner = doing(tmp_path, exit_codes=[1, 0, 0])
    assert team.red_check(context(worktree), TASK, TESTS).ok  # unit-tests fail before the code: red
    result = team.red_check(context(worktree), TASK, TESTS)
    assert not result.ok and "already pass" in result.detail
    guard = TestPlan(cases=[{**TESTS.cases[0].model_dump(), "expected_pass": True}])
    team, runner = doing(tmp_path, exit_codes=[0])
    assert team.red_check(context(worktree), TASK, guard).ok
    assert runner.calls[0][2:] == (False, False)  # no network, read-only


def test_setup_runs_once_with_network(worktree, tmp_path):
    runner = FakeRunner([0, 0, 0])
    gate = ValidationGate(policy(setup=["npm", "ci"]), worktree, tmp_path / "logs", runner=runner)
    gate.run(["unit-tests"], "a")
    gate.run(["unit-tests"], "b")
    assert [call[2:] for call in runner.calls] == [(True, True), (False, False), (False, False)]
    ok, detail, _ = red_check(ValidationGate(policy(red_check=False), worktree, tmp_path, runner=runner), [], False, "x")
    assert ok and "disabled" in detail


# ------------------------------------------------------------------ executor arguments

def test_claude_session_has_no_shell_and_a_path_guard(tmp_path):
    session = Session("developer", "p", tmp_path, ["src/a.js"], [".env"], [["npm", "test"]], 60, tmp_path / "l.log")
    args = ClaudeExecutor().args(session)
    assert args[args.index("--permission-mode") + 1] == "dontAsk"
    assert "Bash" not in args[args.index("--tools") + 1]
    settings = json.loads(args[args.index("--settings") + 1])
    assert "guard.py" in settings["hooks"]["PreToolUse"][0]["hooks"][0]["command"]


def test_claude_result_and_usage_are_parsed(tmp_path):
    output = json.dumps({"result": "changed src/a.js", "is_error": False, "total_cost_usd": 0.12, "num_turns": 4,
                         "usage": {"output_tokens": 900}})

    def runner(args, cwd, timeout, input_text, env):
        assert "ANTHROPIC_API_KEY" not in env and json.loads(env["SDLC_GUARD_ALLOW"]) == ["src/a.js"]
        return ProcessResult(args, 0, output, 2.0)
    session = Session("developer", "p", tmp_path, ["src/a.js"], [], [], 60, tmp_path / "l.log")
    result = ClaudeExecutor(runner=runner).run(session)
    assert result.ok and result.message == "changed src/a.js" and result.usage["turns"] == 4


def test_codex_runs_sandboxed_without_network(tmp_path):
    args = CodexExecutor().args(Session("qa_engineer", "p", tmp_path, [], [], [], 60, tmp_path / "l"), tmp_path / "m")
    assert args[args.index("--sandbox") + 1] == "workspace-write"
    assert "sandbox_workspace_write.network_access=false" in args and args[-1] == "-"


# ------------------------------------------------------------------ the real container

@pytest.mark.skipif(shutil.which("docker") is None or subprocess.run(["docker", "info"], capture_output=True).returncode,
                    reason="docker is not running")
def test_validation_runs_in_a_container_without_network(tmp_path):
    repo = tmp_path / "repo"
    (repo / "test").mkdir(parents=True)
    (repo / "package.json").write_text('{"type": "module", "scripts": {"test": "node --test"}}\n')
    (repo / "test" / "a.test.js").write_text(
        "import test from 'node:test'; import assert from 'node:assert';\n"
        "test('math', () => assert.equal(1 + 1, 2));\n"
        "test('no network', async () => { await assert.rejects(fetch('https://example.com')); });\n")
    gate = ValidationGate(policy(validation_image="node:20-alpine", setup=[]), repo, tmp_path / "logs")
    reports = gate.run(["unit-tests"], "real")
    assert reports[0].ok, reports[0].tail
    (repo / "test" / "b.test.js").write_text("import { writeFileSync } from 'node:fs';\n"
                                             "import test from 'node:test';\n"
                                             "test('read-only', () => writeFileSync('x.txt', 'x'));\n")
    assert not gate.run(["unit-tests"], "readonly")[0].ok


# ------------------------------------------------------------------ GitHub

from controller.app.doing import github  # noqa: E402


def test_repo_slug():
    assert github.repo_slug("https://github.com/congdanh910/ai-native-product-demo") == "congdanh910/ai-native-product-demo"
    assert github.repo_slug("git@github.com:o/r.git") == "o/r"
    assert github.repo_slug("/Users/x/repo") is None


class FakeGh:
    def __init__(self, outputs):
        self.outputs, self.calls = list(outputs), []

    def __call__(self, args, timeout, input_text=None, **_):
        self.calls.append((args, input_text))
        return ProcessResult(args, 0, self.outputs.pop(0), 0.1)


def test_draft_pr_is_created_once_and_updated_afterwards():
    gh = FakeGh(["[]", "https://github.com/o/r/pull/12\n"])
    assert github.open_draft("o/r", "main", "sdlc/run-1", "[SDLC] Due dates", "report", runner=gh) == (
        12, "https://github.com/o/r/pull/12")
    create = gh.calls[1][0]
    assert "--draft" in create and create[create.index("--base") + 1] == "main" and gh.calls[1][1] == "report"
    gh = FakeGh(['[{"number": 12, "url": "u"}]', ""])
    assert github.open_draft("o/r", "main", "sdlc/run-1", "t", "report v2", runner=gh) == (12, "u")
    assert gh.calls[1][0][:3] == ["gh", "pr", "edit"]


def test_pull_state_summarises_ci():
    def state(checks):
        data = {"state": "OPEN", "headRefOid": "a" * 40, "mergeCommit": None, "statusCheckRollup": checks,
                "reviewDecision": None}
        return github.pull_state("o/r", 1, runner=FakeGh([json.dumps(data)])).ci_status
    assert state([]) == "none"
    assert state([{"conclusion": "SUCCESS"}, {"conclusion": "SKIPPED"}]) == "success"
    assert state([{"conclusion": "SUCCESS"}, {"status": "IN_PROGRESS"}]) == "pending"
    assert state([{"conclusion": "FAILURE"}, {"status": "IN_PROGRESS"}]) == "failure"


def test_access_needs_push_permission():
    assert "can push" in github.check_access("o/r", runner=FakeGh(['{"push": true, "private": false}']))
    with pytest.raises(github.GitHubError, match="cannot push"):
        github.check_access("o/r", runner=FakeGh(['{"push": false, "private": false}']))
