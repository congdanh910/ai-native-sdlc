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
    checked: list[str] = []
    monkeypatch.setattr("controller.app.projects.registry.check_project",
                        lambda session, project, actor: checked.append(project.key))
    with session_scope() as session:
        coordinator.poll_pull_requests(session)
    assert status(runs[0]) == "WAITING_PR_APPROVAL" and checked == []
    with session_scope() as session:
        coordinator.poll_pull_requests(session)
    with session_scope() as session:
        pull = session.scalar(select(PullRequest).where(PullRequest.run_id == runs[0]))
        assert (status(runs[0]), pull.merged_sha, pull.ci_status) == ("COMPLETED", "b" * 40, "success")
    assert checked == ["task-board"]  # the merge is checked at once, without a Check again


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


class UnclearRuling(DisputingCli):
    """The QA Engineer cannot follow the first decision and says so; the second one it follows."""

    def __init__(self):
        super().__init__()
        self.unclear = False

    def run(self, session: Session) -> SessionResult:
        if session.role == "qa_engineer" and "Decision on the dispute" in session.prompt and not self.unclear:
            self.unclear = True
            self.sessions.append(session)
            return SessionResult(self.name, True, "UNCLEAR: the decision names no test", 0.5, 0, False)
        return super().run(session)


def test_a_decision_the_qa_engineer_cannot_follow_goes_back_to_the_tech_lead(db, sample_repo, tmp_path):
    run_id = _to_coding(sample_repo)
    cli = UnclearRuling()
    drain(team(cli, FakeContainer(), tmp_path))
    _decide(run_id, "amend_tests", reason="fix it")
    drain(team(cli, FakeContainer(), tmp_path))
    with session_scope() as session:
        run = session.get(Run, run_id)
        assert run.status == "NEEDS_HUMAN" and "QA Engineer finds the decision on the dispute unclear" in run.status_reason
    _decide(run_id, "amend_tests", reason="test/tasks.test.js must expect the priority field")
    drain(team(cli, FakeContainer(), tmp_path))
    assert status(run_id) == "WAITING_PR_APPROVAL"


def coordinator_report(session, run_id) -> str:
    from controller.app import coordinator
    return coordinator.execution_report(session, session.get(Run, run_id))


def test_the_code_owner_can_ask_for_a_new_review(db, sample_repo, tmp_path):
    from controller.app import services
    from controller.app.documents import latest_document
    run_id = _to_coding(sample_repo)
    drain(team(FakeCli(), FakeContainer(), tmp_path))
    with session_scope() as session:
        services.pr_decision(session, services.get_run(session, run_id), actor="tester", decision="rereview",
                             reason="the first review saw no diff")
    assert status(run_id) == "REVIEWING"
    drain(team(FakeCli(), FakeContainer(), tmp_path))
    with session_scope() as session:
        assert status(run_id) == "WAITING_PR_APPROVAL" and latest_document(session, run_id, "review").version == 2


def test_signed_github_deliveries_are_handled_once(db, monkeypatch):
    import hashlib
    import hmac
    from fastapi.testclient import TestClient
    from controller.app import coordinator
    from controller.app.config import get_settings
    from controller.app.main import app

    monkeypatch.setattr(get_settings(), "github_webhook_secret", "s3cret")
    polls = []
    monkeypatch.setattr(coordinator, "poll_pull_requests", lambda session: polls.append(1))
    body = b'{"action": "closed"}'
    headers = {"X-Hub-Signature-256": "sha256=" + hmac.new(b"s3cret", body, hashlib.sha256).hexdigest(),
               "X-GitHub-Delivery": "d-1", "X-GitHub-Event": "pull_request", "Content-Type": "application/json"}
    client = TestClient(app)
    assert client.post("/api/webhooks/github", content=body, headers=headers).json() == {"status": "processed"}
    assert client.post("/api/webhooks/github", content=body, headers=headers).json() == {"status": "duplicate"}
    assert client.post("/api/webhooks/github", content=body,
                       headers={**headers, "X-Hub-Signature-256": "sha256=0"}).status_code == 401
    assert polls == [1]


class LimitedOnceCli(FakeCli):
    """The first Developer session hits the subscription limit after the tests were committed."""

    def __init__(self):
        super().__init__()
        self.limited = False

    def run(self, session: Session) -> SessionResult:
        if session.role == "developer" and not self.limited:
            self.limited = True
            return SessionResult(self.name, False, "You've hit your usage limit.", 1.0, 1, False)
        return super().run(session)


def test_a_subscription_limit_postpones_the_task_and_keeps_its_tests(db, sample_repo, tmp_path):
    from controller.app import jobs
    run_id = _to_coding(sample_repo)
    cli = LimitedOnceCli()
    drain(team(cli, FakeContainer(), tmp_path))
    with session_scope() as session:
        execution = session.scalar(select(Execution).where(Execution.run_id == run_id, Execution.kind == "code_task"))
        assert execution.status == "queued" and "usage limit" in execution.error
        assert execution.available_at > jobs.now()
        execution.available_at = jobs.now()  # the window reopened
    assert status(run_id) == "CODING"
    drain(team(cli, FakeContainer(), tmp_path))
    assert status(run_id) == "WAITING_PR_APPROVAL"
    assert sum(s.startswith("test(T1)") for s in _subjects(run_id)) == 1


def test_normal_runs_pin_the_default_engines_at_creation(db, sample_repo, monkeypatch):
    import json
    from controller.app import coordinator
    from controller.app.config import get_settings
    from controller.tests.integration.test_team_loop import add_project, new_run
    monkeypatch.setattr(get_settings(), "sdlc_engines", "tech_lead=claude-readonly")
    run_id = new_run(add_project(sample_repo))
    monkeypatch.setattr(get_settings(), "sdlc_engines", "")  # a later change does not touch the run
    with session_scope() as session:
        assert json.loads(coordinator.artifact(session, run_id, "engines"))["tech_lead"] == "claude-readonly"


def test_team_report_counts_roles_and_engines(db, sample_repo, tmp_path):
    from controller.app.reports import team_report
    run_id = _to_coding(sample_repo)
    drain(team(FakeCli(), FakeContainer(), tmp_path))
    with session_scope() as session:
        report = team_report(session)
        tasks = session.scalars(select(Task).where(Task.run_id == run_id)).all()
    assert report["runs"]["total"] == 1 and report["runs"]["by_status"] == {"WAITING_PR_APPROVAL": 1}
    assert report["roles"]["developer"]["tasks_passing_validation_first_time"] == len(tasks)
    assert report["roles"]["qa_engineer"]["test_sessions"] == len(tasks)
    assert report["roles"]["tech_lead"]["first_plans_grounded"] == 1.0
    assert report["engines"]["codex"]["calls"] == 2 * len(tasks)
