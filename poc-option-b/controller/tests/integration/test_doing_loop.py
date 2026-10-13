"""The B4 doing roles in the real loop: real worktree and commits, fake CLIs and a fake container."""

from pathlib import Path
import subprocess

import pytest
from sqlalchemy import select

from controller.app.db import session_scope
from controller.app.doing.executors import Session, SessionResult
from controller.app.doing.process import ProcessResult
from controller.app.doing.team import CliDoing
from controller.app.doing.validation import ValidationGate
from controller.app.models import Execution, Run, Task
from controller.app.team.composite import CompositeTeam
from controller.app.team.mock import MockTeam
from controller.tests.integration.test_team_loop import _to_coding, drain, status

pytestmark = pytest.mark.integration


class FakeCli:
    """QA sessions add a new test file; Developer sessions append to the first file of the task."""

    def __init__(self, name="codex"):
        self.name, self.sessions = name, []

    def run(self, session: Session) -> SessionResult:
        self.sessions.append(session)
        if session.role == "qa_engineer":
            path = session.worktree / "test" / f"sdlc-{len(self.sessions)}.test.js"
            path.write_text("test('new behaviour', () => {});\n")
        else:
            target = session.worktree / session.writable[0]
            target.write_text((target.read_text() if target.exists() else "") + "// implemented\n")
        return SessionResult(self.name, True, "done", 0.5, 0, False, {"turns": 2})


class FakeContainer:
    """`npm test` fails while the last commit is a test commit (red), and passes once code follows."""

    def __init__(self, green_red_checks: int = 0):
        self.green_red_checks = green_red_checks

    def __call__(self, image, worktree: Path, argv, timeout, network=False, writable=False):
        if argv[:2] != ["npm", "test"]:  # syntax checks pass either way
            return ProcessResult(["docker", *argv], 0, "ok", 0.1)
        last = subprocess.run(["git", "log", "-1", "--format=%s"], cwd=worktree, capture_output=True, text=True).stdout
        red = last.startswith("test(")
        if red and self.green_red_checks:
            self.green_red_checks -= 1
            red = False
        return ProcessResult(["docker", *argv], 1 if red else 0, "1 failing" if red else "all pass", 0.2)


def team(cli: FakeCli, container: FakeContainer, tmp_path) -> CompositeTeam:
    doing = CliDoing({"codex": cli, "claude": cli}, log_root=tmp_path / "logs",
                     gate_factory=lambda p, w, l: ValidationGate(p.model_copy(update={"setup": []}), w, l,
                                                                 runner=container))
    return CompositeTeam(MockTeam(), doing)


def test_tests_first_then_code_with_commits_per_role(db, sample_repo, tmp_path):
    run_id = _to_coding(sample_repo)
    cli = FakeCli()
    drain(team(cli, FakeContainer(), tmp_path))
    assert status(run_id) == "WAITING_PR_APPROVAL"
    with session_scope() as session:
        run = session.get(Run, run_id)
        tasks = session.scalars(select(Task).where(Task.run_id == run_id)).all()
        executions = session.scalars(select(Execution).where(Execution.run_id == run_id,
                                                             Execution.kind == "code_task")).all()
        worktree = Path(run.worktree_path)
    assert {t.status for t in tasks} == {"PASSED"}
    assert {e.engine for e in executions} == {"codex"}
    steps = executions[0].result["steps"]
    assert [s["state"] for s in steps] == ["WRITING_TESTS", "RED_CHECK", "CODING", "VALIDATING"]
    assert steps[0]["evidence"]["commit"] and steps[1]["evidence"]["reports"][0]["exit_code"] == 1
    log = subprocess.run(["git", "log", "--format=%s", "main.."], cwd=worktree, capture_output=True, text=True).stdout
    subjects = [line for line in log.splitlines()[::-1] if not line.startswith("sdlc:")]
    assert [s.split(":")[0] for s in subjects] == [f"{kind}(T{i})" for i in range(1, len(tasks) + 1)
                                                    for kind in ("test", "feat")]


def test_a_red_check_that_passes_goes_back_to_the_qa_engineer_once(db, sample_repo, tmp_path):
    run_id = _to_coding(sample_repo)
    cli = FakeCli()
    drain(team(cli, FakeContainer(green_red_checks=1), tmp_path))
    with session_scope() as session:
        first = session.scalars(select(Execution).where(Execution.run_id == run_id, Execution.kind == "code_task")
                                .order_by(Execution.created_at)).first()
    states = [s["state"] for s in first.result["steps"]]
    assert states[:4] == ["WRITING_TESTS", "RED_CHECK", "WRITING_TESTS", "RED_CHECK"]
    assert "already pass" in first.result["steps"][1]["detail"]
    assert "already pass" in cli.sessions[1].prompt  # the QA Engineer saw why its first tests were rejected
    assert status(run_id) == "WAITING_PR_APPROVAL"


def test_red_check_failing_twice_needs_a_human(db, sample_repo, tmp_path):
    run_id = _to_coding(sample_repo)
    drain(team(FakeCli(), FakeContainer(green_red_checks=2), tmp_path))
    with session_scope() as session:
        run = session.get(Run, run_id)
        assert run.status == "NEEDS_HUMAN" and "RED_CHECK" in run.status_reason


def test_polling_completes_only_on_a_merge_of_the_pushed_head(db, sample_repo, tmp_path, monkeypatch):
    from controller.app import coordinator
    from controller.app.doing.github import PullState
    from controller.app.models import PullRequest

    runs = [_to_coding(sample_repo)]
    drain(team(FakeCli(), FakeContainer(), tmp_path))
    with session_scope() as session:
        pull = session.scalar(select(PullRequest).where(PullRequest.run_id == runs[0]))
        pull.simulated, pull.number = False, 7
        head = pull.head_sha
    monkeypatch.setattr(coordinator, "repo_slug", lambda url: "o/r")
    states = iter([PullState("OPEN", head, None, "pending", "pending"),
                   PullState("MERGED", head, "b" * 40, "success", "approved")])
    monkeypatch.setattr(coordinator.github, "pull_state", lambda slug, number: next(states))
    with session_scope() as session:
        coordinator.poll_pull_requests(session)
    assert status(runs[0]) == "WAITING_PR_APPROVAL"
    with session_scope() as session:
        coordinator.poll_pull_requests(session)
    with session_scope() as session:
        pull = session.scalar(select(PullRequest).where(PullRequest.run_id == runs[0]))
        assert (status(runs[0]), pull.merged_sha, pull.ci_status) == ("COMPLETED", "b" * 40, "success")


def test_a_merge_of_another_head_needs_a_human(db, sample_repo, tmp_path, monkeypatch):
    from controller.app import coordinator
    from controller.app.doing.github import PullState
    from controller.app.models import PullRequest

    run_id = _to_coding(sample_repo)
    drain(team(FakeCli(), FakeContainer(), tmp_path))
    with session_scope() as session:
        pull = session.scalar(select(PullRequest).where(PullRequest.run_id == run_id))
        pull.simulated, pull.number = False, 8
    monkeypatch.setattr(coordinator, "repo_slug", lambda url: "o/r")
    monkeypatch.setattr(coordinator.github, "pull_state",
                        lambda slug, number: PullState("MERGED", "c" * 40, "d" * 40, "success", "approved"))
    with session_scope() as session:
        coordinator.poll_pull_requests(session)
    assert status(run_id) == "NEEDS_HUMAN"


class DisputingCli(FakeCli):
    """The first Developer session disputes; QA sessions in amend mode edit an existing test line."""

    def __init__(self):
        super().__init__()
        self.disputed = False

    def run(self, session: Session) -> SessionResult:
        if session.role == "developer" and not self.disputed:
            self.disputed = True
            self.sessions.append(session)
            return SessionResult(self.name, True, "DISPUTE: an existing test asserts the old task shape", 0.5, 0, False)
        if session.role == "qa_engineer" and "Decision on the dispute" in session.prompt:
            self.sessions.append(session)
            (session.worktree / "test" / "tasks.test.js").write_text("// existing tests, updated for priority\n")
            return SessionResult(self.name, True, "updated", 0.5, 0, False)
        return super().run(session)


def _decide(run_id, decision, reason="the existing test pins the old shape"):
    from controller.app import services
    with session_scope() as session:
        services.resume(session, services.get_run(session, run_id), actor="tester", decision=decision, reason=reason)


def _subjects(run_id) -> list[str]:
    with session_scope() as session:
        worktree = Path(session.get(Run, run_id).worktree_path)
    log = subprocess.run(["git", "log", "--format=%s", "main.."], cwd=worktree, capture_output=True, text=True).stdout
    return [line for line in log.splitlines()[::-1] if not line.startswith("sdlc:")]


def test_a_ruled_dispute_lets_the_qa_engineer_update_existing_tests(db, sample_repo, tmp_path):
    run_id = _to_coding(sample_repo)
    cli = DisputingCli()
    drain(team(cli, FakeContainer(), tmp_path))
    assert status(run_id) == "NEEDS_HUMAN"
    _decide(run_id, "amend_tests")
    drain(team(cli, FakeContainer(), tmp_path))
    assert status(run_id) == "WAITING_PR_APPROVAL"
    subjects = _subjects(run_id)
    assert subjects[:3] == [subjects[0], "test(T1): update tests after a ruled dispute", subjects[2]]
    assert subjects[2].startswith("feat(T1)") and sum(s.startswith("test(T1)") for s in subjects) == 2
    with session_scope() as session:
        report = coordinator_report(session, run_id)
    assert "QA Engineer updated the tests" in report and "test/tasks.test.js (+1/-1)" in report


def test_kept_tests_send_the_developer_back_with_the_decision(db, sample_repo, tmp_path):
    run_id = _to_coding(sample_repo)
    cli = DisputingCli()
    drain(team(cli, FakeContainer(), tmp_path))
    _decide(run_id, "keep_tests", reason="the old shape is still required")
    drain(team(cli, FakeContainer(), tmp_path))
    assert status(run_id) == "WAITING_PR_APPROVAL"
    developer = [s for s in cli.sessions if s.role == "developer"][1]
    assert "The Tech Lead kept the tests: the old shape is still required" in developer.prompt
    assert sum(s.startswith("test(T1)") for s in _subjects(run_id)) == 1  # the tests were reused, not rewritten


def coordinator_report(session, run_id) -> str:
    from controller.app import coordinator
    return coordinator.execution_report(session, session.get(Run, run_id))
