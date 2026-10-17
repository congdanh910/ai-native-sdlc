import shutil

import pytest
from sqlalchemy import select

from controller.app import evals
from controller.app.db import session_scope
from controller.app.models import EvalRun, Execution, Run
from controller.app.projects import registry
from controller.app.config import PROJECT_ROOT
from controller.tests.conftest import git
from controller.tests.integration.test_team_loop import OPERATOR, drain

pytestmark = pytest.mark.integration


@pytest.fixture
def suite_dir(tmp_path, sample_repo, monkeypatch):
    """The Task Board suite, pointed at the sample repository's head."""
    root = tmp_path / "evals"
    shutil.copytree(PROJECT_ROOT / "evals" / "task-board", root / "task-board")
    head = git(sample_repo, "rev-parse", "HEAD").strip()
    suite = root / "task-board" / "suite.yaml"
    text = suite.read_text().replace("69c0dd74c3329bdc7e9b981cde82ab4fca201aa5", head)
    suite.write_text(text)
    monkeypatch.setattr(evals, "EVALS_DIR", root)
    return root


def test_feature_files_become_intents():
    suite = evals.load_suite("task-board")
    assert [case.id for case in suite.cases] == ["search-filter", "due-dates", "priority", "archive", "backup"]
    due = suite.cases[1].intent
    assert due.title == "Due dates" and due.outcome.count("\n- ") == 4


def test_plan_only_eval_scores_against_golden_plans(db, sample_repo, suite_dir):
    with session_scope() as session:
        registry.add_project(session, "task-board", str(sample_repo), actor=OPERATOR)
        evaluation_id = evals.start_eval(session, "task-board", actor=OPERATOR).id
    drain()
    with session_scope() as session:
        assert evals.advance(session) == [evaluation_id]
    with session_scope() as session:
        evaluation = session.get(EvalRun, evaluation_id)
        summary, cases = evaluation.results["summary"], evaluation.results["cases"]
        runs = session.scalars(select(Run).where(Run.eval_run_id == evaluation_id)).all()
        assert evaluation.status == "completed" and summary["cases"] == 5 and summary["planned"] == 5
        assert summary["grounding_pass_v1"] == 1.0
        source = {"index.html", "src/app.js", "src/tasks.js", "styles.css"}
        assert all(source <= set(c["golden_files"]) and c["file_recall"] is not None for c in cases)
        assert {run.status for run in runs} == {"CANCELLED"} and all(run.experiment for run in runs)
        assert all(run.stage_modes["spec"] == "auto" for run in runs)
        assert evaluation.fingerprint["prompts"]["plan"]
        second = evals.start_eval(session, "task-board", actor=OPERATOR)
        assert second.baseline_id == evaluation_id


def test_eval_needs_its_project(db, suite_dir):
    with session_scope() as session:
        with pytest.raises(evals.services.DecisionError, match="register project"):
            evals.start_eval(session, "task-board", actor=OPERATOR)


def test_an_eval_can_put_a_thinking_role_on_a_readonly_cli(db, sample_repo, suite_dir):
    import json
    from controller.app import coordinator
    from controller.app.models import Run
    from controller.app.services import DecisionError
    from sqlalchemy import select
    with session_scope() as session:
        registry.add_project(session, "task-board", str(sample_repo), actor=OPERATOR)
    with session_scope() as session:
        evaluation = evals.start_eval(session, "task-board", actor=OPERATOR, engines={"tech_lead": "claude-readonly"})
        assert evaluation.fingerprint["engines"]["tech_lead"] == "claude-readonly"
        run_id = session.scalars(select(Run.id).where(Run.eval_run_id == evaluation.id)).first()
    from controller.app.config import get_settings
    from controller.app.team.mock import MockTeam
    from controller.app.team.readonly import RoutingTeam

    from controller.app.team.base import CallRecord

    class FakeReadonly(MockTeam):
        engine = "claude-readonly"

        def plan(self, context):
            self.last_call = CallRecord("claude-readonly", "p", 1, usage={"seconds": 3.0, "cost_usd": 0.02})
            return super().plan(context)
    drain(RoutingTeam(MockTeam(), get_settings(), factory=lambda cli: FakeReadonly()))  # never calls a real CLI
    with session_scope() as session:
        run = session.get(Run, run_id)
        assert json.loads(coordinator.artifact(session, run_id, "engines"))["tech_lead"] == "claude-readonly"
        assert coordinator.build_context(session, run).engines["tech_lead"] == "claude-readonly"
    with session_scope() as session:
        engines = {e for e in session.scalars(select(Execution.engine).where(Execution.run_id == run_id,
                                                                            Execution.kind == "plan"))}
    assert engines == {"claude-readonly"}
