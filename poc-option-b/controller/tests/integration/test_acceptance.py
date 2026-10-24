"""Acceptance: after the review, E2E tests on the preview and the QC's gate; defects go back to the Developer."""

import re

import pytest

from controller.app import services
from controller.app.acceptance import coverage, parse_report, render_acceptance
from controller.app.db import session_scope
from controller.app.documents import latest_document
from controller.app.models import Run
from controller.app.states import RunStatus
from controller.app.team.mock import MockTeam
from controller.tests.conftest import git
from controller.tests.integration.test_team_loop import (OPERATOR, TICKS, add_project, approve_plan, approve_spec, drain,
                                                         new_run, status)

pytestmark = pytest.mark.integration
PREVIEW = ("preview:\n  compose: .sdlc/preview.compose.yml\n  service: web\n  port: 8080\n"
           "  e2e: [npx, playwright, test]\n")


def with_preview(repo, mode: str = "gated") -> None:
    config = repo / ".sdlc" / "project.yaml"
    text = config.read_text() + PREVIEW
    text = re.sub(r"^(stages:.*)$", rf"\1\n  acceptance: {mode}", text, count=1, flags=re.MULTILINE)
    config.write_text(text)
    git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "--quiet", "-am", "preview")


class Recording(MockTeam):
    def __init__(self):
        self.notes = []

    def implement(self, context, task, attempt, tests=None, feedback=""):
        self.notes.append(feedback)
        return super().implement(context, task, attempt, tests, feedback)

    def review(self, context, plan, tests, diff="", validation=""):
        self.notes += [f"review decision: {d}" for d in context.decisions]
        self.notes += [f"review note: {n}" for n in context.platform_notes]
        return super().review(context, plan, tests, diff=diff, validation=validation)

    def write_acceptance_tests(self, context, criteria, story, feedback=""):
        self.notes.append(f"acceptance: {feedback}")
        return super().write_acceptance_tests(context, criteria, story, feedback=feedback)


def test_the_story_is_accepted_on_its_preview_after_its_defects_are_fixed(db, sample_repo):
    with_preview(sample_repo)
    run_id = new_run(add_project(sample_repo))
    drain()
    approve_spec(run_id)
    drain()
    approve_plan(run_id)
    drain()
    assert status(run_id) == RunStatus.WAITING_ACCEPTANCE_APPROVAL
    with session_scope() as session:
        report = latest_document(session, run_id, "acceptance")
        assert report.data["problems"] == [] and all(c["status"] == "passed" for c in report.data["cases"])
        run = services.get_run(session, run_id)
        with pytest.raises(services.DecisionError, match="describe the defects"):
            services.decide_acceptance(session, run, actor=OPERATOR, decision="reject", reason="")
        services.decide_acceptance(session, run, actor=OPERATOR, decision="reject",
                                   reason="AC-1: the due date is not shown in the list")
    team = Recording()
    drain(team)
    assert status(run_id) == RunStatus.WAITING_ACCEPTANCE_APPROVAL  # coding, review and acceptance again
    assert any("QC (acceptance): AC-1: the due date is not shown in the list" in n for n in team.notes)
    assert any(n.startswith("acceptance: The QC sent defects back") for n in team.notes)  # the tests may follow it
    # the review knows the QC's decision and how the preview runs, so it does not report them as defects
    assert any(n.startswith("review decision: QC defects: AC-1") for n in team.notes)
    assert any(n.startswith("review note: The preview runs") for n in team.notes)
    with session_scope() as session:
        report = latest_document(session, run_id, "acceptance")
        assert report.version == 2
        services.decide_acceptance(session, services.get_run(session, run_id), actor=OPERATOR, decision="accept",
                                   document_id=report.id, checked=TICKS)
    drain()
    with session_scope() as session:
        assert session.get(Run, run_id).status == RunStatus.WAITING_PR_APPROVAL


def test_auto_acceptance_opens_the_pr_when_every_criterion_passes(db, sample_repo):
    with_preview(sample_repo, mode="auto")
    run_id = new_run(add_project(sample_repo))
    drain()
    approve_spec(run_id)
    drain()
    approve_plan(run_id)
    drain()
    assert status(run_id) == RunStatus.WAITING_PR_APPROVAL


def test_without_a_preview_the_review_opens_the_pr(db, sample_repo):
    run_id = new_run(add_project(sample_repo))
    drain()
    approve_spec(run_id)
    drain()
    approve_plan(run_id)
    drain()
    assert status(run_id) == RunStatus.WAITING_PR_APPROVAL


def test_a_playwright_report_gives_evidence_per_criterion(tmp_path):
    evidence = tmp_path
    (evidence / "shots").mkdir()
    (evidence / "shots" / "a.png").write_bytes(b"png")
    report = {"suites": [{"title": "employees.spec.ts", "specs": [], "suites": [{"title": "Employees", "specs": [
        {"title": "AC-01 HR admin creates an employee", "tests": [{"results": [{"status": "passed", "attachments": [
            {"name": "screenshot", "contentType": "image/png", "path": "/evidence/shots/a.png"},
            {"name": "missing", "contentType": "image/png", "path": "/evidence/nope.png"}]}]}]},
        {"title": "EC-01 duplicate code is refused", "tests": [{"results": [
            {"status": "failed", "error": {"message": "\x1b[31mexpected 409\x1b[0m"}, "attachments": []}]}]}]}]}]}
    cases = parse_report(report, evidence)
    assert [(c["criteria"], c["status"]) for c in cases] == [(["AC-01"], "passed"), (["EC-01"], "failed")]
    assert cases[0]["attachments"] == [{"name": "screenshot", "type": "image/png", "path": "shots/a.png"}]
    assert cases[1]["error"] == "expected 409"
    assert coverage(["AC-01", "EC-01", "AC-02"], cases) == ["EC-01 failed: EC-01 duplicate code is refused",
                                                            "AC-02 has no acceptance test and no passing test of the coding"]
    text = render_acceptance("Employees", {"AC-01": "creates", "EC-01": "duplicate", "AC-02": "lists"}, cases,
                             "http://127.0.0.1:9100/", "/api/runs/r/evidence/1")
    assert "| AC-01: creates | AC-01 HR admin creates an employee | passed | [screenshot](/api/runs/r/evidence/1/shots/a.png) |" in text
    assert "| AC-02: lists | — | missing | — |" in text and "## Failures" in text


class BrokenAcceptance(MockTeam):
    def write_acceptance_tests(self, *args, **kwargs):
        raise RuntimeError("the acceptance session could not start")


def test_a_failed_acceptance_job_is_resumed_at_acceptance(db, sample_repo):
    with_preview(sample_repo)
    run_id = new_run(add_project(sample_repo))
    drain()
    approve_spec(run_id)
    drain()
    approve_plan(run_id)
    for _ in range(4):  # the job is retried, then given up
        drain(BrokenAcceptance())
        with session_scope() as session:
            from controller.app.models import Execution
            for execution in session.query(Execution).filter(Execution.status == "queued"):
                execution.available_at = None
    assert status(run_id) == RunStatus.NEEDS_HUMAN
    with session_scope() as session:
        services.resume(session, services.get_run(session, run_id), actor=OPERATOR, decision="resume",
                        reason="the acceptance session is fixed")
    drain()
    assert status(run_id) == RunStatus.WAITING_ACCEPTANCE_APPROVAL


def _at_the_qc_gate(sample_repo) -> str:
    with_preview(sample_repo)
    (sample_repo / "SDS.md").write_text("# SDS\n\n## 6. API Design\n\nUse 400 for invalid field format.\n")
    git(sample_repo, "-c", "user.name=t", "-c", "user.email=t@t", "add", ".")
    git(sample_repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "--quiet", "-m", "sds")
    run_id = new_run(add_project(sample_repo))
    drain()
    approve_spec(run_id)
    drain()
    approve_plan(run_id)
    drain()
    assert status(run_id) == RunStatus.WAITING_ACCEPTANCE_APPROVAL
    return run_id


def _fix_tasks(run_id) -> list:
    from sqlalchemy import select
    from controller.app.models import Task
    with session_scope() as session:
        return [t.payload["defects"] for t in session.scalars(select(Task).where(Task.run_id == run_id))
                if t.payload.get("defects")]


def test_defect_notes_that_agree_with_the_sds_go_to_the_fix_task(db, sample_repo):
    run_id = _at_the_qc_gate(sample_repo)
    with session_scope() as session:
        services.decide_acceptance(session, services.get_run(session, run_id), actor=OPERATOR, decision="reject",
                                   reason="AC-1: missing fields must return 400 (SDS 6)")
    drain()
    assert _fix_tasks(run_id) == ["QC (acceptance): AC-1: missing fields must return 400 (SDS 6)"]
    assert status(run_id) == RunStatus.WAITING_ACCEPTANCE_APPROVAL  # fixed, reviewed, tested again


def test_defect_notes_that_contradict_the_sds_go_back_to_the_person_first(db, sample_repo):
    from controller.app.api.views import run_detail
    run_id = _at_the_qc_gate(sample_repo)
    with session_scope() as session:
        services.decide_acceptance(session, services.get_run(session, run_id), actor=OPERATOR, decision="reject",
                                   reason="AC-1: missing fields must return 422 CONTRADICTS-SDS")
    drain()
    assert status(run_id) == RunStatus.NEEDS_HUMAN and _fix_tasks(run_id) == []
    with session_scope() as session:
        run = services.get_run(session, run_id)
        assert "conflict with the SDS" in run.status_reason
        assert run_detail(session, run)["defect_check"]["check"]["points"][0]["verdict"] == "conflict"
        services.resume(session, run, actor=OPERATOR, decision="defects_revised",
                        reason="AC-1: missing fields must return 400 (SDS 6)")
        from sqlalchemy import select
        from controller.app.models import Approval
        last = session.scalars(select(Approval).where(Approval.run_id == run_id, Approval.stage == "acceptance")
                               .order_by(Approval.created_at.desc())).first()
        assert last.reason == "AC-1: missing fields must return 400 (SDS 6)"  # the revised notes, not the first ones
    drain()
    assert _fix_tasks(run_id) == ["QC (acceptance): AC-1: missing fields must return 400 (SDS 6)"]
    assert status(run_id) == RunStatus.WAITING_ACCEPTANCE_APPROVAL


def test_defect_notes_can_be_sent_as_written_against_the_sds(db, sample_repo):
    run_id = _at_the_qc_gate(sample_repo)
    with session_scope() as session:
        services.decide_acceptance(session, services.get_run(session, run_id), actor=OPERATOR, decision="reject",
                                   reason="AC-1: 422 CONTRADICTS-SDS")
    drain()
    with session_scope() as session:
        services.resume(session, services.get_run(session, run_id), actor=OPERATOR, decision="defects_as_written",
                        reason="the SDS is out of date here; 422 is agreed with the customer")
    drain()
    assert _fix_tasks(run_id) == ["QC (acceptance): AC-1: 422 CONTRADICTS-SDS"]


def test_qc_notes_about_the_acceptance_tests_can_go_to_the_tests_instead_of_the_code(db, sample_repo):
    from sqlalchemy import select
    from controller.app.models import Approval
    run_id = _at_the_qc_gate(sample_repo)
    with session_scope() as session:
        services.decide_acceptance(session, services.get_run(session, run_id), actor=OPERATOR, decision="reject",
                                   reason="AC-1 needs its own browser test CONTRADICTS-SDS")
    drain()
    assert status(run_id) == RunStatus.NEEDS_HUMAN
    with session_scope() as session:
        services.resume(session, services.get_run(session, run_id), actor=OPERATOR, decision="defects_retest",
                        reason="AC-1 needs its own browser test")
        last = session.scalars(select(Approval).where(Approval.run_id == run_id)
                               .order_by(Approval.created_at.desc())).first()
        assert (last.stage, last.decision, last.reason) == ("acceptance", "retest", "AC-1 needs its own browser test")
    drain()
    assert _fix_tasks(run_id) == [] and status(run_id) == RunStatus.WAITING_ACCEPTANCE_APPROVAL


def test_notes_no_code_change_can_satisfy_go_back_to_the_person(db, sample_repo):
    run_id = _at_the_qc_gate(sample_repo)
    with session_scope() as session:
        services.decide_acceptance(session, services.get_run(session, run_id), actor=OPERATOR, decision="reject",
                                   reason="AC-2 has no acceptance test NO-CODE-CHANGE")
    drain()
    assert status(run_id) == RunStatus.NEEDS_HUMAN and _fix_tasks(run_id) == []
    with session_scope() as session:
        assert "no code change can do" in services.get_run(session, run_id).status_reason


def test_the_qc_also_ticks_that_the_screens_follow_the_ui_concept(db, sample_repo):
    (sample_repo / "docs" / "ui").mkdir(parents=True)
    (sample_repo / "docs" / "ui" / "UI.md").write_text("# UI\n")
    git(sample_repo, "-c", "user.name=t", "-c", "user.email=t@t", "add", ".")
    git(sample_repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "--quiet", "-m", "ui")
    with_preview(sample_repo)
    run_id = new_run(add_project(sample_repo))
    drain()
    approve_spec(run_id)
    drain()
    approve_plan(run_id)
    drain()
    from controller.app.api.views import run_detail
    with session_scope() as session:
        run = services.get_run(session, run_id)
        assert "ui-concept" in [h["id"] for h in run_detail(session, run)["checklist"]["human"]]
        report = latest_document(session, run_id, "acceptance")
        with pytest.raises(services.DecisionError, match="UI concept"):
            services.decide_acceptance(session, run, actor=OPERATOR, decision="accept", document_id=report.id,
                                       checked=TICKS)
        services.decide_acceptance(session, run, actor=OPERATOR, decision="accept", document_id=report.id,
                                   checked=[*TICKS, "ui-concept"])


def _with_regressions(monkeypatch, *errors):
    """The mock acceptance run, plus earlier stories' tests failing with these errors until the QA Engineer is told
    to update them."""
    from controller.app import coordinator
    accept = coordinator._accept

    def run(team, loaded):
        result = accept(team, loaded)
        if result["ok"] and "update AC-17" not in loaded.extra.get("feedback", ""):
            result["cases"] = result["cases"] + [
                {"title": f"AC-17 earlier story check {n}", "criteria": [], "regression": True, "file": "e2e/access.spec.ts",
                 "status": "failed", "error": error, "attachments": []} for n, error in enumerate(errors)]
        return result
    monkeypatch.setattr(coordinator, "_accept", run)


def test_earlier_stories_tests_outdated_by_the_story_are_updated_without_the_qc(db, sample_repo, monkeypatch):
    from sqlalchemy import select
    from controller.app.models import Approval, Event
    _with_regressions(monkeypatch, "OUTDATED: button 'Manage accounts' not found")
    team = Recording()
    with_preview(sample_repo)
    run_id = new_run(add_project(sample_repo))
    drain(team)
    approve_spec(run_id)
    drain(team)
    approve_plan(run_id)
    drain(team)
    assert status(run_id) == RunStatus.WAITING_ACCEPTANCE_APPROVAL  # the second run passed: no problem left
    assert any("update AC-17 earlier story check 0" in n for n in team.notes if n.startswith("acceptance: "))
    with session_scope() as session:
        assert latest_document(session, run_id, "acceptance").data["problems"] == []
        retest = session.scalar(select(Approval).where(Approval.run_id == run_id, Approval.decision == "retest"))
        assert retest.actor == "code_reviewer (auto)" and "Decided by the Code Reviewer" in retest.reason
        assert session.scalar(select(Event).where(Event.run_id == run_id, Event.event_type == "acceptance.auto_retest"))


def test_a_test_the_story_broke_goes_to_the_qc_with_the_judgement(db, sample_repo, monkeypatch):
    from controller.app.api.views import _regression_triage
    _with_regressions(monkeypatch, "Error: 500 Internal Server Error")
    with_preview(sample_repo)
    run_id = new_run(add_project(sample_repo))
    drain()
    approve_spec(run_id)
    drain()
    approve_plan(run_id)
    drain()
    assert status(run_id) == RunStatus.WAITING_ACCEPTANCE_APPROVAL
    with session_scope() as session:
        run = session.get(Run, run_id)
        assert latest_document(session, run_id, "acceptance").data["problems"][0].startswith("regression: ")
        triage = _regression_triage(session, run)
        assert triage["tests"][0]["verdict"] == "broken" and triage["note"] == ""


class FindingReviewer(Recording):
    """The first review finds an important issue (the SDS supports it unless it names CONTRADICTS-SDS); later reviews
    pass."""
    def __init__(self, finding="Account creation has no Idempotency-Key handling"):
        super().__init__()
        self.finding, self.reviews = finding, 0

    def review(self, context, plan, tests, diff="", validation=""):
        self.reviews += 1
        review = super().review(context, plan, tests, diff=diff, validation=validation)
        if self.reviews == 1:
            review.verdict, review.findings = "concerns", [{"severity": "important", "text": self.finding,
                                                            "file": "src/tasks.js"}]
        return review


def _sds(repo) -> None:
    (repo / "SDS.md").write_text("# SDS\n\n## 6. API Design\n\nRetries reuse the Idempotency-Key.\n")
    git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "add", ".")
    git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "--quiet", "-m", "sds")


def test_important_review_findings_go_to_a_fix_task_before_the_acceptance_tests(db, sample_repo):
    from sqlalchemy import select
    from controller.app.models import Event
    with_preview(sample_repo)
    _sds(sample_repo)
    team = FindingReviewer()
    run_id = new_run(add_project(sample_repo))
    drain(team)
    approve_spec(run_id)
    drain(team)
    approve_plan(run_id)
    drain(team)
    assert status(run_id) == RunStatus.WAITING_ACCEPTANCE_APPROVAL and team.reviews == 2
    fixes = _fix_tasks(run_id)
    assert len(fixes) == 1 and fixes[0].startswith("Code Reviewer (review): 1. Account creation has no Idempotency-Key")
    with session_scope() as session:
        assert session.scalar(select(Event).where(Event.run_id == run_id, Event.event_type == "review.findings_sent"))


def test_review_findings_the_sds_contradicts_are_dropped_without_a_person(db, sample_repo):
    with_preview(sample_repo)
    _sds(sample_repo)
    team = FindingReviewer("CONTRADICTS-SDS: use 422 for every invalid field")
    run_id = new_run(add_project(sample_repo))
    drain(team)
    approve_spec(run_id)
    drain(team)
    approve_plan(run_id)
    drain(team)
    assert status(run_id) == RunStatus.WAITING_ACCEPTANCE_APPROVAL and team.reviews == 1 and _fix_tasks(run_id) == []


def test_earlier_stories_tests_the_story_broke_go_to_a_fix_task_before_the_qc(db, sample_repo, monkeypatch):
    from sqlalchemy import select
    from controller.app.models import Event
    _with_regressions(monkeypatch, "Error: expected 200, received 500")
    with_preview(sample_repo)
    _sds(sample_repo)
    team = Recording()  # the mock judges a test broken unless its error names OUTDATED
    run_id = new_run(add_project(sample_repo))
    drain(team)
    approve_spec(run_id)
    drain(team)
    approve_plan(run_id)
    drain(team)
    fixes = _fix_tasks(run_id)
    assert len(fixes) >= 1 and "AC-17 earlier story check 0 (an earlier story's acceptance test) fails" in fixes[0]
    assert "expected 200, received 500" in fixes[0]
    with session_scope() as session:
        sent = session.scalars(select(Event).where(Event.run_id == run_id,
                                                   Event.event_type == "acceptance.regressions_sent")).all()
    assert 1 <= len(sent) <= 2  # disputes.max_auto_per_task, then the QC decides
    assert status(run_id) == RunStatus.WAITING_ACCEPTANCE_APPROVAL
