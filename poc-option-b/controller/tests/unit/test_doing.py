import dataclasses
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
from controller.app.projects.config import ValidationCommand, effective_policy, parse_project_config
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


def test_tests_rejected_by_the_red_check_are_rewritten_not_appended(worktree, tmp_path):
    qa = FakeExecutor("claude", {"test/due.test.js": "test('due', () => {});\n"})
    team, _ = doing(tmp_path, qa=qa)
    team.write_tests(context(worktree, qa_executor="claude"), TASK, TESTS)
    team.write_tests(context(worktree, qa_executor="claude"), TASK, TESTS, feedback="already pass")
    assert git(worktree, "log", "--format=%s").splitlines()[:2] == ["test(T1): Add due date", "initial"]


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
    syntax_only = TASK.model_copy(update={"validation": ["syntax"]})  # the red check still runs the tests
    team, runner = doing(tmp_path, exit_codes=[1, 0, 0, 0])
    assert team.red_check(context(worktree), syntax_only, TESTS).ok  # unit-tests fail before the code: red
    assert [call[1][0] for call in runner.calls] == ["npm", "node"]
    result = team.red_check(context(worktree), TASK, TESTS)
    assert not result.ok and "already pass" in result.detail
    guard = TestPlan(cases=[{**TESTS.cases[0].model_dump(), "expected_pass": True}])
    team, runner = doing(tmp_path, exit_codes=[0, 0])
    assert team.red_check(context(worktree), TASK, guard).ok
    assert runner.calls[0][2:] == (False, False)  # no network, read-only


def test_tests_under_a_manifest_the_task_creates_are_red_by_construction(worktree, tmp_path):
    web = TASK.model_copy(update={"create": ["web/package.json", "web/src/app.ts"]})
    tests = TestPlan(cases=[{**TESTS.cases[0].model_dump(), "target_file": "web/tests/app.test.ts"}])
    team, _ = doing(tmp_path, exit_codes=[0] * 6)  # the commands skip web/ while web/package.json is missing
    result = team.red_check(context(worktree, setup_inputs=["web/package.json"]), web, tests)
    assert result.ok and "red by construction" in result.detail and "web/package.json" in result.detail
    assert not team.red_check(context(worktree, setup_inputs=["web/package.json"]), TASK, tests).ok
    (worktree / "web").mkdir()
    (worktree / "web/package.json").write_text("{}")  # the manifest exists: the tests could have failed
    assert not team.red_check(context(worktree, setup_inputs=["web/package.json"]), web, tests).ok


def test_the_qa_engineer_writes_acceptance_tests_in_the_e2e_paths(worktree, tmp_path):
    from controller.app.projects.config import Preview
    preview = Preview(compose=".sdlc/preview.compose.yml", service="web", port=8000, e2e_paths=["test/**"])
    qa = FakeExecutor("claude", {"test/due.spec.ts": "test('AC-1 due date', async () => {});\n"})
    team, _ = doing(tmp_path, qa=qa)
    result = team.write_acceptance_tests(context(worktree, qa_executor="claude", preview=preview), "- AC-1: due", "Due")
    assert result.ok and result.changed_files == ["test/due.spec.ts"], result.detail
    assert qa.sessions[0].writable == ["test/**"]


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
    assert "--sandbox" not in args and 'default_permissions="sdlc"' in args  # the profile, not the older flag
    assert "permissions.sdlc.network.enabled=false" in args and args[-1] == "-"
    filesystem = next(a for a in args if a.startswith("permissions.sdlc.filesystem="))
    assert '":workspace_roots" = {"." = "write"}' in filesystem and f'"{Path.home()}" = "read"' not in filesystem


def test_the_tools_on_path_are_readable_but_not_the_home_folder(tmp_path):
    from controller.app.doing.sandbox import tool_roots
    home = tmp_path / "home"
    for folder in ("bin", ".nvm/v20/bin", "opt/tool/bin", "opt/sdk"):
        (home / folder).mkdir(parents=True)
    path = ":".join(str(home / f) for f in ("bin", ".nvm/v20/bin", "opt/tool/bin", "opt/sdk")) + ":relative/bin"
    assert tool_roots(path, home) == [(home / "bin").resolve(), (home / ".nvm/v20").resolve(),
                                      (home / "opt/tool").resolve(), (home / "opt/sdk").resolve()]


def test_claude_reads_stay_in_the_worktree(tmp_path):
    def reason(tool, **tool_input):
        return decide({"tool_name": tool, "tool_input": tool_input, "cwd": str(tmp_path)}, tmp_path, [], [])
    assert reason("Read", file_path="src/a.js") is None
    assert "outside the repository" in reason("Read", file_path="/Users/someone/other-project/README.md")
    assert "outside the repository" in reason("Read", file_path="~/.ssh/id_ed25519")
    assert reason("Grep", pattern="def |class ") is None  # a regular expression, not a path
    assert "outside the repository" in reason("Grep", pattern="x", path="/Users/someone/other-project")
    assert "outside the repository" in reason("Glob", pattern="/Users/someone/**/*.json")
    assert reason("Glob", pattern="**/*.ts", path="web") is None


def test_each_command_runs_in_its_own_image_with_its_own_setup(worktree, tmp_path):
    """Several languages without one big image: python tests in python, web tests in node, each set up once."""
    (worktree / "requirements.txt").write_text("v1\n")
    commands = [ValidationCommand(name="py", run=["pytest"], image="python-3.13", setup=["pip", "install", "-r", "requirements.txt"]),
                ValidationCommand(name="lint", run=["ruff", "check"], image="python-3.13", setup=["pip", "install", "-r", "requirements.txt"]),
                ValidationCommand(name="web", run=["npm", "test"], image="node:22-alpine", setup=["npm", "ci"])]
    runner = FakeRunner([0] * 20)
    gate = ValidationGate(policy(validation_image="", setup=[], validation=commands, setup_inputs=["requirements.txt"]),
                          worktree, tmp_path / "logs", runner=runner)
    assert all(r.ok for r in gate.run([], "a"))
    assert [(image, argv[0], net) for image, argv, net, _ in runner.calls] == [
        ("python:3.13-slim", "pip", True), ("python:3.13-slim", "pytest", False), ("python:3.13-slim", "ruff", False),
        ("node:22-alpine", "npm", True), ("node:22-alpine", "npm", False)]  # the python setup ran once for two commands
    runner.calls.clear()
    (worktree / "requirements.txt").write_text("v2\n")
    gate.run([], "b")
    assert [argv[0] for _, argv, net, _ in runner.calls if net] == ["pip", "npm"]  # a changed input sets both up again


def test_a_project_may_have_no_image_of_its_own_when_every_command_names_one():
    base = (PROJECT_ROOT / "examples/task-board/.sdlc/project.yaml").read_text()
    import re
    text = re.sub(r"validation_image: node-20.*", "", base)
    text = re.sub(r"setup: .*", "setup: []", text)
    text = re.sub(r"validation:.*\n(  - .*\n|    .*\n)+", "validation:\n  - name: unit-tests\n    run: [npm, test]\n    image: node-22\n", text)
    assert effective_policy(parse_project_config(text)).validation[0].image == "node-22"
    with pytest.raises(ValueError, match="image on every validation command"):
        parse_project_config(text.replace("    image: node-22\n", ""))


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


@pytest.mark.skipif(shutil.which("docker") is None or subprocess.run(["docker", "info"], capture_output=True).returncode
                    or subprocess.run(["docker", "image", "inspect", "postgres:17-alpine"], capture_output=True).returncode,
                    reason="docker or the postgres:17-alpine image is not available")
def test_a_project_environment_builds_its_image_and_reaches_its_services(tmp_path):
    from controller.app.projects.config import Service
    repo = tmp_path / "repo"
    (repo / ".sdlc").mkdir(parents=True)
    (repo / ".sdlc" / "validation.Dockerfile").write_text("FROM postgres:17-alpine\n")  # psql as the toolchain
    (repo / "requirements.txt").write_text("v1\n")
    probe = ValidationCommand(name="db", run=["sh", "-c", "psql -h postgres -U app -d app -tAc 'select 40 + 2' && "
                                              "! wget -q -T 3 -O /dev/null https://example.com"], timeout_seconds=120)
    gate = ValidationGate(policy(validation_image="", env_dockerfile=".sdlc/validation.Dockerfile", project="t",
                                 validation=[probe], setup=["sh", "-c", "cat requirements.txt > setup.out"],
                                 setup_inputs=["requirements.txt"], env={"PGPASSWORD": "app"},
                                 services=[Service(name="postgres", image="postgres:17-alpine",
                                                   env={"POSTGRES_USER": "app", "POSTGRES_PASSWORD": "app", "POSTGRES_DB": "app"},
                                                   ready=["pg_isready", "-U", "app", "-h", "127.0.0.1"])]),
                          repo, tmp_path / "logs")
    reports = gate.run(["db"], "env")
    assert reports[0].ok, reports[0].tail
    assert "42" in Path(reports[0].log_path).read_text()  # the service answered; the Internet did not
    assert (repo / "setup.out").read_text() == "v1\n"
    (repo / "requirements.txt").write_text("v2\n")  # a changed dependency manifest runs setup again
    assert gate.run(["db"], "env2")[0].ok and (repo / "setup.out").read_text() == "v2\n"
    from controller.app.doing.process import CONTAINER_PREFIX  # the tests' own prefix: a live run's network is not ours
    left = subprocess.run(["docker", "network", "ls", "-q", "--filter", f"name={CONTAINER_PREFIX}net-"], capture_output=True,
                          text=True).stdout.split()
    assert left == []  # services and their network are removed
    image, _ = gate.image()
    subprocess.run(["docker", "rmi", "-f", image], capture_output=True)  # the test leaves no image behind


@pytest.mark.skipif(shutil.which("docker") is None or subprocess.run(["docker", "info"], capture_output=True).returncode
                    or subprocess.run(["docker", "image", "inspect", "python:3.12-slim"], capture_output=True).returncode,
                    reason="docker or the python:3.12-slim image is not available")
def test_a_preview_starts_on_the_loopback_and_stops(tmp_path, monkeypatch):
    import urllib.request
    from controller.app import preview
    from controller.app.projects.config import Preview
    repo = tmp_path / "repo"
    (repo / ".sdlc").mkdir(parents=True)
    (repo / "site").mkdir()
    (repo / "site" / "health").write_text("ok")
    (repo / ".sdlc" / "preview.compose.yml").write_text(
        "services:\n  web:\n    image: python:3.12-slim\n    working_dir: /site\n    volumes: [./site:/site:ro]\n"
        "    command: [python, -m, http.server, '8000']\n")
    config = Preview(compose=".sdlc/preview.compose.yml", service="web", port=8000, health_path="/health",
                     start_timeout_seconds=60, accounts=[{"role": "ADMIN", "username": "a@example.test", "password": "x"}])
    run_id = "testprev" + "0" * 28
    port = preview.free_port(set())
    try:
        state = preview.start(run_id, repo, config, port, tmp_path / "logs")
        assert state["status"] == "running", state["detail"]
        assert state["url"] == f"http://127.0.0.1:{port}/" and state["expires_at"] > state["started_at"]
        assert urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=5).read() == b"ok"
        published = subprocess.run(["docker", "compose", "-p", preview.project_name(run_id), "port", "web", "8000"],
                                   capture_output=True, text=True).stdout.strip()
        assert published == f"127.0.0.1:{port}"  # never on the machine's other interfaces
    finally:
        assert preview.stop(run_id)["status"] == "stopped"
    assert preview.free_port(set()) == port  # the port is free again


@pytest.mark.skipif(shutil.which("docker") is None or subprocess.run(["docker", "info"], capture_output=True).returncode
                    or subprocess.run(["docker", "image", "inspect", "python:3.12-slim"], capture_output=True).returncode,
                    reason="docker or the python:3.12-slim image is not available")
def test_a_preview_that_fails_says_which_service_failed_and_why_and_leaves_nothing_running(tmp_path):
    from controller.app import preview
    from controller.app.projects.config import Preview
    repo = tmp_path / "repo"
    (repo / ".sdlc").mkdir(parents=True)
    (repo / ".sdlc" / "preview.compose.yml").write_text(
        "services:\n  seed:\n    image: python:3.12-slim\n    command: [python, -m, app.seed_demo]\n"
        "  web:\n    image: python:3.12-slim\n    command: [python, -m, http.server, '8000']\n"
        "    depends_on:\n      seed:\n        condition: service_completed_successfully\n")
    config = Preview(compose=".sdlc/preview.compose.yml", service="web", port=8000, start_timeout_seconds=60)
    run_id = "testfail" + "0" * 28
    state = preview.start(run_id, repo, config, preview.free_port(set()), tmp_path / "logs")
    assert state["status"] == "failed"
    assert "seed (exit 1)" in state["detail"] and "app.seed_demo" in state["detail"].split("compose:")[0]
    left = subprocess.run(["docker", "ps", "-aq", "--filter", f"label=com.docker.compose.project={preview.project_name(run_id)}"],
                          capture_output=True, text=True).stdout.strip()
    assert left == ""


@pytest.mark.skipif(shutil.which("docker") is None or subprocess.run(["docker", "info"], capture_output=True).returncode
                    or any(subprocess.run(["docker", "image", "inspect", i], capture_output=True).returncode
                           for i in ("python:3.12-slim", "node:22-alpine")),
                    reason="docker or the python and node images are not available")
def test_two_languages_validate_in_their_own_containers(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    commands = [ValidationCommand(name="py", run=["python", "-c", "import sys; print(sys.version_info[0])"], image="python-3.12"),
                ValidationCommand(name="js", run=["node", "-e", "console.log(process.release.name)"], image="node-22")]
    gate = ValidationGate(policy(validation_image="", setup=[], validation=commands), repo, tmp_path / "logs")
    reports = gate.run([], "langs")
    assert [r.ok for r in reports] == [True, True], [r.tail for r in reports]
    assert "3" in Path(reports[0].log_path).read_text() and "node" in Path(reports[1].log_path).read_text()


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
    assert gh.calls[1][0][:4] == ["gh", "api", "--method", "PATCH"] and json.loads(gh.calls[1][1]) == {"body": "report v2"}


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


def test_review_diff_puts_sources_first_and_leaves_out_the_thread(worktree):
    base = changes.head(worktree)
    (worktree / "src" / "tasks.js").write_text("export const PRIORITIES = ['Low'];\n")
    (worktree / "test" / "tasks.test.js").write_text("// existing tests\n" + "test('x', () => {});\n" * 200)
    (worktree / ".sdlc" / "runs").mkdir(parents=True)
    (worktree / ".sdlc" / "runs" / "spec.md").write_text("# Spec\n")
    changes.stage_changes(worktree)
    changes.commit(worktree, "feat", "developer")
    diff = changes.run_diff(worktree, base, ["test/**"], budget=1500)
    assert diff.index("src/tasks.js") < diff.index("test/tasks.test.js") and ".sdlc" not in diff
    assert "truncated by the context budget" in diff and len(diff) < 1600


# ------------------------------------------------------------------ B5: limits, timeouts, cancellation

def test_a_subscription_limit_postpones_instead_of_failing(worktree, tmp_path):
    from controller.app.team.base import SubscriptionLimit
    team, _ = doing(tmp_path, dev=FakeExecutor("codex", message="You've hit your usage limit. Try again at 3pm.",
                                               ok=False))
    with pytest.raises(SubscriptionLimit) as raised:
        team.implement(context(worktree), TASK, 1, TESTS)
    assert raised.value.wait_seconds == 900


def test_a_failing_session_is_not_mistaken_for_a_limit(worktree, tmp_path):
    team, _ = doing(tmp_path, dev=FakeExecutor("codex", message="SyntaxError in src/tasks.js", ok=False))
    result = team.implement(context(worktree), TASK, 1, TESTS)
    assert not result.ok and "Developer session failed" in result.detail


def test_a_timed_out_session_fails_the_step(tmp_path):
    def runner(args, cwd, timeout, input_text, env):
        return ProcessResult(args, None, "still thinking", timeout, timed_out=True)
    result = ClaudeExecutor(runner=runner).run(Session("developer", "p", tmp_path, [], [], [], 1, tmp_path / "l"))
    assert not result.ok and result.timed_out and not result.limited


def test_cancellation_stops_the_running_process_and_any_later_one():
    import threading
    from controller.app.doing import process
    process.begin_job()
    threading.Timer(0.5, process.cancel_active).start()
    result = run_bounded(["sleep", "30"], timeout=20)
    assert result.exit_code is None and "cancelled" in result.output and result.seconds < 10
    assert "cancelled" in run_bounded(["echo", "hi"], timeout=5).output  # nothing new starts
    process.begin_job()
    assert run_bounded(["echo", "hi"], timeout=5).output.strip() == "hi"


def test_orphaned_sessions_are_stopped_at_worker_start(tmp_path, monkeypatch):
    from controller.app.doing import process
    monkeypatch.setattr(process, "SESSIONS_DIR", tmp_path / "sessions")
    orphan = subprocess.Popen(["sleep", "30"], start_new_session=True)
    (tmp_path / "sessions").mkdir()
    (tmp_path / "sessions" / f"{orphan.pid}.json").write_text(json.dumps({"pid": orphan.pid, "program": "sleep"}))
    stopped = process.kill_orphans()
    assert f"sleep pid {orphan.pid}" in stopped and orphan.wait(timeout=5) is not None
    assert not list((tmp_path / "sessions").iterdir())


def test_webhook_signature():
    body = b'{"action": "closed"}'
    import hashlib
    import hmac
    good = "sha256=" + hmac.new(b"s3cret", body, hashlib.sha256).hexdigest()
    assert github.verify_signature("s3cret", body, good)
    assert not github.verify_signature("s3cret", body + b" ", good)
    assert not github.verify_signature("", body, good)


def test_mirror_fetch_skips_pushed_run_branches(sample_repo, tmp_path):
    from controller.app.projects.repo import Mirror, Worktree
    mirror = Mirror(tmp_path / "m.git")
    mirror.clone(str(sample_repo))
    run = Worktree(mirror, tmp_path / "wt", "sdlc/run-1")
    run.create(mirror.resolve("main"))
    run.commit_file("notes.md", "local work\n", "local")
    git(sample_repo, "branch", "sdlc/run-1")  # the branch was pushed for a draft PR and differs from the worktree
    (sample_repo / "new.txt").write_text("x")
    git(sample_repo, "add", ".")
    git(sample_repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "--quiet", "-m", "upstream")
    mirror.fetch()  # used to fail: refusing to fetch into a branch checked out in a worktree
    assert mirror.resolve("main") == git(sample_repo, "rev-parse", "main").strip()
    assert (tmp_path / "wt" / "notes.md").exists()


def test_widening_an_import_is_not_an_edit_of_existing_tests(worktree):
    (worktree / "test" / "tasks.test.js").write_text(
        "import { addTask } from '../src/tasks.js';\n// existing tests\n")
    git(worktree, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qam", "imports")
    (worktree / "test" / "tasks.test.js").write_text(
        "import { addTask, filterTasks } from '../src/tasks.js';\n// existing tests\ntest('x', () => {});\n")
    assert changes.check_tests(worktree, changes.stage_changes(worktree), policy()) == []
    changes.discard(worktree)
    (worktree / "test" / "tasks.test.js").write_text(
        "import { filterTasks } from '../src/tasks.js';\n// existing tests\n")  # drops a name: an edit
    assert "existing line" in changes.check_tests(worktree, changes.stage_changes(worktree), policy())[0]


def test_a_name_added_inside_a_multi_line_import_is_not_an_edit(worktree):
    path = worktree / "test" / "test_identity.py"
    path.write_text("from conftest import (\n    NOW, account,\n    session,\n)\n\n\ndef test_a():\n    call(\n        a, b,\n    )\n")
    git(worktree, "-c", "user.name=t", "-c", "user.email=t@t", "add", ".")
    git(worktree, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "imports")
    path.write_text(path.read_text().replace("    session,\n)", "    session, revoke_all,\n)") + "\n\ndef test_b():\n    pass\n")
    assert changes.check_tests(worktree, changes.stage_changes(worktree), policy(test_paths=["test/**"])) == []
    changes.discard(worktree)
    path.write_text(path.read_text().replace("        a, b,\n", "        a, b, c,\n"))  # an argument list, not an import
    assert "existing line" in changes.check_tests(worktree, changes.stage_changes(worktree), policy(test_paths=["test/**"]))[0]


def test_session_timeout_comes_from_the_project_limits(worktree, tmp_path):
    dev = FakeExecutor("codex", edits={"src/tasks.js": "export const due = 1;\n"})
    team, _ = doing(tmp_path, dev=dev)
    team.implement(context(worktree, session_timeout_seconds=2700), TASK, 1, TESTS)
    assert dev.sessions[-1].timeout == 2700


def test_every_subscription_limit_message_is_recognised():
    from controller.app.doing.executors import LIMIT
    for message in ("Claude AI usage limit reached", "You've hit your session limit · resets 11:50pm (Asia/Saigon)",
                    "You've hit your weekly limit", "429 Too Many Requests"):
        assert LIMIT.search(message), message
    assert not LIMIT.search("AssertionError: expected 1 to equal 2")


def test_the_tech_lead_plans_with_the_preview_compose_file(worktree):
    from controller.app.projects.config import Preview
    from controller.app.team.langflow_team import LangflowTeam
    preview = Preview(compose=".sdlc/preview.compose.yml", service="web", port=8000,
                      accounts=[{"role": "ADMIN", "username": "demo.admin", "password": "x"}])
    assert LangflowTeam._preview(context(worktree, preview=preview)) is None  # no compose file yet
    (worktree / ".sdlc" / "preview.compose.yml").write_text("services:\n  seed:\n    command: [python, -m, app.seed_demo]\n")
    sent = LangflowTeam._preview(context(worktree, preview=preview))
    assert "app.seed_demo" in sent["compose"] and sent["accounts"] == [{"role": "ADMIN", "username": "demo.admin"}]


class LockRunner:
    """The lock command writes the lockfile (or another file); validation commands pass."""

    def __init__(self, writes: dict[str, str], lock_exit: int = 0):
        self.writes, self.lock_exit, self.calls = writes, lock_exit, []

    def __call__(self, image, worktree, argv, timeout, network=False, writable=False):
        self.calls.append((argv, network, writable))
        if argv[:1] == ["lock"]:
            for path, text in self.writes.items():
                (worktree / path).write_text(text)
            return ProcessResult(["docker", *argv], self.lock_exit, "npm ERR! notarget" if self.lock_exit else "", 0.1)
        return ProcessResult(["docker", *argv], 0, "ok", 0.1)


def locking(tmp_path, runner):
    return CliDoing({"codex": FakeExecutor("codex"), "claude": FakeExecutor("claude")},
                    gate_factory=lambda p, w, l: ValidationGate(p, w, l, runner=runner), log_root=tmp_path / "logs")


def test_the_platform_locks_dependencies_after_a_manifest_changes(worktree, tmp_path):
    base = changes.head(worktree)
    (worktree / "package.json").write_text('{"dependencies": {"left-pad": "^1.3.0"}}\n')
    changes.stage_changes(worktree)
    changes.commit(worktree, "feat(T1): Add due date", "developer")
    lock_policy = dict(setup_inputs=["package.json", "package-lock.json"], lock=["lock"])
    ctx = dataclasses.replace(context(worktree, **lock_policy), base_commit=base)
    runner = LockRunner({"package-lock.json": '{"lockfileVersion": 3}\n'})
    team = locking(tmp_path, runner)
    assert team.validate(ctx, TASK).ok
    assert runner.calls[0] == (["lock"], True, True)  # with network, the worktree writable
    assert git(worktree, "log", "-1", "--format=%s").strip() == "build(T1): lock dependencies"
    assert team.validate(ctx, TASK).ok and [c for c in runner.calls if c[0] == ["lock"]] == [runner.calls[0]]  # once

    changes.discard(worktree)
    (worktree / "package.json").write_text('{"dependencies": {"left-pad": "^99"}}\n')
    changes.stage_changes(worktree)
    changes.commit(worktree, "feat(T1): Add due date (attempt 2)", "developer")
    failed = locking(tmp_path, LockRunner({}, lock_exit=1)).validate(ctx, TASK)
    assert not failed.ok and "the lock command failed" in failed.detail and "notarget" in failed.evidence["failures"]

    other = locking(tmp_path, LockRunner({"package-lock.json": "{}\n", "src/tasks.js": "// hm\n"})).validate(ctx, TASK)
    assert not other.ok and "src/tasks.js" in other.detail and git(worktree, "status", "--porcelain") == ""


def test_without_a_manifest_change_nothing_is_locked(worktree, tmp_path):
    base = changes.head(worktree)
    (worktree / "src" / "tasks.js").write_text("export const due = 1;\n")
    changes.stage_changes(worktree)
    changes.commit(worktree, "feat(T1): Add due date", "developer")
    runner = LockRunner({})
    ctx = dataclasses.replace(context(worktree, setup_inputs=["package.json"], lock=["lock"]), base_commit=base)
    assert locking(tmp_path, runner).validate(ctx, TASK).ok and all(c[0] != ["lock"] for c in runner.calls)


def test_the_tests_a_commit_adds_and_the_earlier_commits_of_a_file(worktree):
    base = changes.head(worktree)
    (worktree / "src" / "tasks.js").write_text("export const due = 1;\n")
    changes.stage_changes(worktree)
    built = changes.commit(worktree, "feat(T2): Add due date", "developer")
    plan_base = changes.head(worktree)
    (worktree / "test" / "due.test.js").write_text("it('TC-1 keeps the due date', () => {});\n")
    (worktree / "test" / "test_due.py").write_text("def test_tc2_rejects_a_past_date():\n    pass\n")
    changes.stage_changes(worktree)
    tests = changes.commit(worktree, "test(T2): Add due date", "qa_engineer")
    assert changes.test_names(worktree, tests) == [("test/due.test.js", "TC-1 keeps the due date"),
                                                   ("test/test_due.py", "test_tc2_rejects_a_past_date")]
    earlier = changes.commits_touching(worktree, base, plan_base, ["src/tasks.js", "src/other.js"])
    assert [(c["sha"], c["subject"], c["files"]) for c in earlier] == [(built, "feat(T2): Add due date", ["src/tasks.js"])]


def test_the_qa_engineer_sees_the_preview_and_sends_an_unclear_ruling_back(worktree, tmp_path):
    from controller.app.projects.config import Preview
    (worktree / ".sdlc" / "preview.compose.yml").write_text("services:\n  api:\n    build: ./backend\n")
    git(worktree, "add", ".")
    git(worktree, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "preview")
    preview = Preview(compose=".sdlc/preview.compose.yml", service="api", port=8000)
    qa = FakeExecutor("claude", message="UNCLEAR: the decision says 201 but TC-1 says 200; which one?")
    team, _ = doing(tmp_path, qa=qa)
    result = team.write_tests(context(worktree, qa_executor="claude", preview=preview), TASK, TESTS,
                              feedback="use 201", amend=True)
    assert result.dispute and not result.ok and "which one" in result.detail
    prompt = qa.sessions[0].prompt
    assert "build: ./backend" in prompt and "the decision wins over the test cases" in prompt


def test_a_brief_from_a_model_is_repaired_into_the_contract():
    from controller.app.schemas import Advice
    from controller.app.team.normalize import advice
    brief = Advice.model_validate(advice({
        "situation": "Two tests disagree.", "recommendation": "Amend tests", "confidence": "High",
        "reason": "test_t3_tc1 expects 201", "checks": "look at line 137",
        "sides": ["the code returns 201", {"party": "TC-1", "claim": "200", "evidence": "TC-1: return 200"}]}))
    assert brief.recommendation == "amend_tests" and brief.confidence == "high" and brief.checks == ["look at line 137"]
    assert brief.sides[1].evidence == ["TC-1: return 200"]


class LintRunner:
    """unit-tests fail (red); the lint command fails naming the given file."""

    def __init__(self, lint_file: str, tests_exit: int = 1):
        self.lint_file, self.tests_exit = lint_file, tests_exit

    def __call__(self, image, worktree, argv, timeout, network=False, writable=False):
        if argv[:1] == ["ruff"]:
            return ProcessResult(["docker", *argv], 1, f"{self.lint_file}:45:9: F402 Import `secret` shadowed", 0.1)
        return ProcessResult(["docker", *argv], self.tests_exit, "exit", 0.1)


def test_lint_failures_in_the_new_tests_go_back_to_the_qa_engineer(worktree, tmp_path):
    base = changes.head(worktree)
    (worktree / "test" / "due.test.js").write_text("test('due', () => {});\n")
    changes.stage_changes(worktree)
    changes.commit(worktree, "test(T1): Add due date", "qa_engineer")
    commands = [ValidationCommand(name="unit-tests", run=["npm", "test"]), ValidationCommand(name="python-lint", run=["ruff", "check"])]
    ctx = dataclasses.replace(context(worktree, validation=commands), base_commit=base)

    def team(runner):
        return CliDoing({"codex": FakeExecutor("codex"), "claude": FakeExecutor("claude")},
                        gate_factory=lambda p, w, l: ValidationGate(p, w, l, runner=runner), log_root=tmp_path / "logs")
    result = team(LintRunner("test/due.test.js")).red_check(ctx, TASK, TESTS)
    assert not result.ok and "fail the project's lint" in result.detail and "F402" in result.detail
    assert team(LintRunner("src/tasks.js")).red_check(ctx, TASK, TESTS).ok  # lint of other files: not the QA's
    only_lint = team(LintRunner("src/tasks.js", tests_exit=0)).red_check(ctx, TASK, TESTS)
    assert not only_lint.ok and "already pass" in only_lint.detail  # a lint failure is never the red of TDD


def test_the_acceptance_run_installs_playwright_at_the_image_version_on_a_copy():
    from controller.app.acceptance import e2e_argv
    from controller.app.projects.config import Preview
    preview = Preview(compose=".sdlc/preview.compose.yml", service="web", port=80, e2e=["npx", "playwright", "test"],
                      e2e_image="mcr.microsoft.com/playwright:v1.56.1-noble", e2e_paths=["e2e/**"])
    argv = e2e_argv(preview)
    assert argv[:2] == ["sh", "-c"] and argv[-3:] == ["npx", "playwright", "test"]
    assert "@playwright/test@1.56.1" in argv[2] and "npm ci --prefix e2e" in argv[2] and "/tmp/work" in argv[2]


class DeletingQA(FakeExecutor):
    """Rewrites the suite and removes an obsolete file of it."""

    def run(self, session):
        (session.worktree / "test" / "tasks.test.js").unlink(missing_ok=True)
        return super().run(session)


def test_acceptance_tests_written_for_an_earlier_preview_are_written_again(worktree, tmp_path):
    from controller.app.projects.config import Preview
    preview = Preview(compose=".sdlc/preview.compose.yml", service="web", port=8000, e2e_paths=["test/**"])
    (worktree / ".sdlc" / "preview.compose.yml").write_text("services:\n  web:\n    build: ./src\n")
    git(worktree, "add", ".")
    git(worktree, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "preview")
    base = git(worktree, "rev-list", "--max-parents=0", "HEAD").strip()
    (worktree / "test" / "due.spec.ts").write_text("test('AC-1 due', async () => {});\n")
    changes.stage_changes(worktree)
    changes.commit(worktree, "test(acceptance): end-to-end tests of the story's criteria", "qa_engineer")
    ctx = dataclasses.replace(context(worktree, qa_executor="claude", preview=preview), base_commit=base)
    qa = DeletingQA("claude", {"test/due.spec.ts": "test('AC-1 due date shows', async () => {});\n"})
    team, _ = doing(tmp_path, qa=qa)
    assert team.write_acceptance_tests(ctx, "- AC-1: due", "Due").evidence.get("reused")  # same preview: reused
    (worktree / ".sdlc" / "preview.compose.yml").write_text("services:\n  web:\n    build: ./web\n")
    git(worktree, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qam", "onboarding v5")
    result = team.write_acceptance_tests(ctx, "- AC-1: due", "Due")
    assert result.ok and not result.evidence.get("reused"), result.detail
    assert "The preview or its demo accounts changed" in qa.sessions[-1].prompt


def test_the_environment_check_starts_the_preview_once_the_code_exists(tmp_path, monkeypatch):
    from controller.app import coordinator, preview
    from controller.app.coordinator import Loaded
    from controller.app.projects.config import Preview
    (tmp_path / ".sdlc").mkdir()
    (tmp_path / ".sdlc" / "preview.compose.yml").write_text("services:\n  api:\n    build: ./backend\n")
    policy = coordinator.EffectivePolicy.model_validate(
        {**policy_dict(), "preview": Preview(compose=".sdlc/preview.compose.yml", service="api", port=8000).model_dump()})
    loaded = Loaded(None, "environment", None, 1)
    loaded.extra.update(worktree=str(tmp_path), run_id="run-1")
    calls = []
    monkeypatch.setattr(preview, "start", lambda *a, **k: calls.append("start") or {"status": "failed", "detail": "seed (exit 1)"})
    monkeypatch.setattr(preview, "stop", lambda *a, **k: calls.append("stop"))
    assert coordinator._try_preview(policy, loaded) is None and calls == []  # backend/ not written yet
    (tmp_path / "backend").mkdir()
    assert coordinator._try_preview(policy, loaded)["detail"] == "seed (exit 1)" and calls == ["start", "stop"]


def policy_dict() -> dict:
    return policy().model_dump()


def test_a_preview_build_that_times_out_is_not_the_architects_fault(tmp_path, monkeypatch):
    from controller.app import coordinator, preview
    from controller.app.coordinator import Loaded
    from controller.app.projects.config import Preview
    (tmp_path / ".sdlc").mkdir()
    (tmp_path / "backend").mkdir()
    (tmp_path / ".sdlc" / "preview.compose.yml").write_text("services:\n  api:\n    build: ./backend\n")
    policy = coordinator.EffectivePolicy.model_validate(
        {**policy_dict(), "preview": Preview(compose=".sdlc/preview.compose.yml", service="api", port=8000).model_dump()})
    loaded = Loaded(None, "environment", None, 1)
    loaded.extra.update(worktree=str(tmp_path), run_id="run-1")
    monkeypatch.setattr(preview, "start", lambda *a, **k: {"status": "failed", "detail": "did not finish", "timed_out": True})
    monkeypatch.setattr(preview, "stop", lambda *a, **k: None)
    assert coordinator._try_preview(policy, loaded)["timed_out"]


def test_errors_in_the_test_code_are_told_apart_from_failures_of_the_application():
    from controller.app.acceptance import authoring_errors
    cases = [{"title": "AC-01 signs in", "status": "failed", "error": "Error: strict mode violation: getByRole(...)"},
             {"title": "AC-24 envelope", "status": "failed", "error": "Expected: 400\nReceived: 422"},
             {"title": "AC-05 loads", "status": "passed", "error": ""}]
    assert authoring_errors(cases) == ["AC-01 signs in: Error: strict mode violation: getByRole(...)"]


def test_tests_failing_in_their_own_code_go_back_to_the_qa_engineer_once(worktree, tmp_path, monkeypatch):
    from types import SimpleNamespace
    from controller.app import acceptance, coordinator, preview
    from controller.app.coordinator import Loaded
    from controller.app.projects.config import Preview
    from controller.app.team.base import StepResult
    monkeypatch.setattr(coordinator, "get_settings", lambda: SimpleNamespace(coding_is_mocked=False,
                                                                            sdlc_preview_ports="9100-9199"))
    monkeypatch.setattr(coordinator, "DATA_DIR", tmp_path)
    monkeypatch.setattr(preview, "free_port", lambda taken: 9100)
    monkeypatch.setattr(preview, "start", lambda *a, **k: {"status": "running", "url": "http://127.0.0.1:9100/"})
    monkeypatch.setattr(acceptance, "run_e2e", lambda *a, **k: {"ok": True, "report": {}, "detail": "e2e exit 1"})
    runs = iter([[{"title": "AC-1 due", "criteria": ["AC-1"], "status": "failed", "attachments": [],
                   "error": "strict mode violation: getByRole('heading') resolved to 2 elements"}],
                 [{"title": "AC-1 due", "criteria": ["AC-1"], "status": "passed", "attachments": [], "error": ""}]])
    monkeypatch.setattr(acceptance, "parse_report", lambda report, evidence, story="": next(runs))

    class QA:
        feedback = []

        def write_acceptance_tests(self, context, criteria, story, feedback=""):
            self.feedback.append(feedback)
            return StepResult(True, "written")
    preview_config = Preview(compose=".sdlc/preview.compose.yml", service="web", port=80)
    loaded = Loaded(None, "acceptance", None, 1)
    loaded.context = context(worktree, preview=preview_config)
    loaded.extra.update(attempt=1, run_id="run-1", evict=[], taken=[], preview=preview_config.model_dump(),
                        worktree=str(worktree))
    qa = QA()
    result = coordinator._accept(qa, loaded)
    assert [c["status"] for c in result["cases"]] == ["passed"] and len(qa.feedback) == 2
    assert "strict mode violation" in qa.feedback[1] and result["tests"]["retry"]["ok"]


def test_a_criterion_the_coding_tests_prove_needs_no_browser_test():
    from controller.app.acceptance import coverage, render_acceptance
    cases = [{"title": "AC-1 signs in", "criteria": ["AC-1"], "status": "passed", "attachments": [], "error": ""}]
    proven = {"AC-2": ["TC-4", "TC-5"]}
    assert coverage(["AC-1", "AC-2", "AC-3"], cases, proven) == [
        "AC-3 has no acceptance test and no passing test of the coding"]
    report = render_acceptance("t", {"AC-1": "a", "AC-2": "b", "AC-3": "c"}, cases, "", "/e", proven)
    assert "proven by the coding's tests TC-4, TC-5" in report


def test_a_fix_task_may_change_any_file_in_the_allowed_paths_but_not_others(worktree):
    fix = TASK.model_copy(update={"modify": ["src/tasks.js", "src/**"], "create": ["src/**"]})
    (worktree / "src" / "migration_0002.js").write_text("// new\n")
    (worktree / "src" / "tasks.js").write_text("export const due = 1;\n")
    (worktree / "src" / "app.js").write_text("// an earlier plan's module\n")
    assert changes.check_code(worktree, changes.stage_changes(worktree), fix, policy()) == []
    changes.discard(worktree)
    (worktree / "README.md").write_text("# changed\n")  # outside the allowed paths
    assert "not in task T1" in "\n".join(changes.check_code(worktree, changes.stage_changes(worktree), fix, policy()))


def test_test_files_the_developer_writes_are_dropped_and_the_code_stays(worktree, tmp_path):
    dev = FakeExecutor("codex", {"src/tasks.js": "export const due = 1;\n", "test/own.test.js": "test('x', () => {});\n"})
    team, _ = doing(tmp_path, dev=dev)
    result = team.implement(context(worktree), TASK, 1, TESTS)
    assert result.ok and result.changed_files == ["src/tasks.js"] and "test/own.test.js" in result.detail
    assert not (worktree / "test" / "own.test.js").exists()
    assert "Tests are the QA Engineer's" in dev.sessions[0].prompt


def test_acceptance_tests_the_qa_engineer_finds_right_after_fixed_defects_are_kept(worktree, tmp_path):
    from controller.app.projects.config import Preview
    preview = Preview(compose=".sdlc/preview.compose.yml", service="web", port=8000, e2e_paths=["test/**"])
    base = changes.head(worktree)
    (worktree / "test" / "list.spec.ts").write_text("test('AC-1 list', async () => {});\n")
    changes.stage_changes(worktree)
    suite = changes.commit(worktree, "test(acceptance): end-to-end tests of the story's criteria", "qa_engineer")
    (worktree / "src" / "tasks.js").write_text("// the fix task's change\n")
    changes.stage_changes(worktree)
    changes.commit(worktree, "feat(T4): Fix the defects found by the QC (acceptance)", "developer")
    qa = FakeExecutor("claude", message="No test changes were needed.")
    team, _ = doing(tmp_path, qa=qa)
    ctx = dataclasses.replace(context(worktree, qa_executor="claude", preview=preview), base_commit=base)
    result = team.write_acceptance_tests(ctx, "- AC-1: list", "List", feedback="The QC sent defects back: AC-1 headers")
    assert result.ok and result.evidence["reused"] and result.evidence["commit"] == suite, result.detail


def test_only_the_story_s_own_acceptance_tests_prove_its_criteria(tmp_path):
    from controller.app.acceptance import coverage, parse_report
    def spec(title, status="passed"):
        return {"title": title, "tests": [{"results": [{"status": status, "attachments": []}]}]}
    report = {"suites": [
        {"title": "auth.spec.ts", "specs": [spec("EC-06 signing out one account leaves the other signed in"),
                                            spec("AC-09 invalid credentials keep both fields", "failed")]},
        {"title": "employee-list.spec.ts", "suites": [{"title": "EMP-US-02", "specs": [spec("AC-01 HR_ADMIN sees the list")]}]}]}
    cases = parse_report(report, tmp_path, "EMP-US-02")
    assert [(c["title"][:5], c["criteria"], c["regression"]) for c in cases] == [
        ("EC-06", [], True), ("AC-09", [], True), ("AC-01", ["AC-01"], False)]
    problems = coverage(["AC-01", "EC-06"], cases)
    assert problems == ["regression: AC-09 invalid credentials keep both fields failed (an acceptance test of an earlier story)",
                        "EC-06 has no acceptance test and no passing test of the coding"]  # the foundation's EC-06 is not this one


def test_validation_tells_a_broken_environment_from_failing_code():
    from controller.app.doing.validation import first_error, infrastructure_problem
    service = {"name": "service-postgres", "ok": False, "exit_code": 1, "tail": "postgres was not ready after 60s"}
    assert infrastructure_problem([service]).startswith("service postgres of the validation environment did not start")
    dns = {"name": "backend-api", "ok": False, "exit_code": 1,
           "tail": "psycopg.OperationalError: failed to resolve host 'postgres': Temporary failure in name resolution"}
    assert "could not be reached" in infrastructure_problem([dns])
    assert "exit 137" in infrastructure_problem([{"name": "backend-unit", "ok": False, "exit_code": 137, "tail": ""}])
    code = {"name": "backend-unit", "ok": False, "exit_code": 1, "tail": "collected 3\nE   assert 404 == 200\nFAILED test_x"}
    assert infrastructure_problem([code]) == "" and first_error([code]) == "backend-unit: E   assert 404 == 200"
