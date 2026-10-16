"""Team API used by the web control room. Every decision is made as SDLC_OPERATOR."""

from pathlib import Path
from typing import Literal
import uuid

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select

from controller.app import evals, joblog, services
from controller.app.api import views
from controller.app.config import get_settings
from controller.app.db import session_scope
from controller.app.discovery import service as discovery
from controller.app.discovery import questions
from controller.app.discovery.service import DiscoveryRequest
from controller.app.models import EvalRun, Execution, Project, Run, Source
from controller.app.projects import registry
from controller.app.project_docs import ArchitectureBrief, ProjectBrief
from controller.app.schemas import Intent
from controller.app.states import STAGE_STARTER, HumanRole
from controller.app.team import get_team

router = APIRouter(prefix="/api")


class Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class NewProject(Body):
    key: str
    repo_url: str
    branch: str | None = None


class DraftRequest(Body):
    title: str = Field(min_length=3, max_length=200)
    text: str = Field(min_length=10)


class NewRun(Body):
    project: str
    intent: Intent | None = None  # a feature request
    brief: ProjectBrief | None = None  # a project brief: the run writes SRS.md
    architecture: ArchitectureBrief | None = None  # an architecture brief: the run writes SDS.md from SRS.md
    discovery: DiscoveryRequest | None = None  # a discovery: raw requirement sources to a baseline
    idempotency_key: str = Field(min_length=8, max_length=128)

    @model_validator(mode="after")
    def _one_input(self) -> "NewRun":
        if sum(x is not None for x in (self.intent, self.brief, self.architecture, self.discovery)) != 1:
            raise ValueError("send exactly one of intent (a feature), brief (the SRS), architecture (the SDS) "
                             "or discovery")
        return self


class Answers(Body):
    answers: dict[str, str]


class DocumentChoice(Body):
    document_id: uuid.UUID
    sha256: str
    checked: list[str] = []  # the human checklist items the reviewer ticked


class PlanChoice(Body):
    plan_id: uuid.UUID
    sha256: str
    checked: list[str] = []


class Revision(Body):
    content: str = Field(min_length=1)
    reason: str | None = None


class PlanRevision(Body):
    plan: dict
    test_plan: dict
    reason: str | None = None


class Rejection(Body):
    reason: str
    next: Literal["redo", "cancel", "replan"] = "redo"  # replan: only at the Quality gate


class TestsRevision(Body):
    test_plan: dict
    reason: str | None = None


class StageStart(Body):
    stage: Literal["spec", "plan", "coding", "review"]


class Resume(Body):
    decision: Literal["resume", "replan", "cancel", "amend_tests", "keep_tests"]
    reason: str


class Reason(Body):
    reason: str = ""


class PrDecision(Body):
    decision: Literal["approve", "checklist", "request_changes", "close", "rereview"]
    reason: str | None = None
    checked: list[str] = []


class Classification(Body):
    category: str
    stage: str
    role: str
    note: str = ""


def operator() -> str:
    return get_settings().sdlc_operator


def _decide(run_id: uuid.UUID, action) -> dict:
    """Run one decision in a transaction and return the updated run."""
    try:
        with session_scope() as session:
            run = services.get_run(session, run_id)
            action(session, run)
        with session_scope() as session:
            return views.run_detail(session, session.get(Run, run_id))
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except (services.DecisionError, ValueError) as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get("/meta")
def meta() -> dict:
    return {"operator": operator(), "team_mode": get_settings().sdlc_team_mode,
            "doing_mode": get_settings().sdlc_doing_mode, "engines": get_settings().default_engines,
            "roles": [role.value for role in HumanRole], "failure_categories": services.FAILURE_CATEGORIES,
            "failure_stages": services.FAILURE_STAGES, "failure_roles": services.FAILURE_ROLES}


@router.get("/projects")
def list_projects() -> list[dict]:
    with session_scope() as session:
        return [views.project_view(p) for p in session.scalars(select(Project).order_by(Project.key))]


@router.post("/projects", status_code=201)
def add_project(body: NewProject) -> dict:
    try:
        with session_scope() as session:
            return views.project_view(registry.add_project(session, body.key, body.repo_url, actor=operator(),
                                                         branch=body.branch))
    except registry.ProjectError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/projects/{key}/check")
def check_project(key: str) -> dict:
    with session_scope() as session:
        project = session.scalar(select(Project).where(Project.key == key))
        if project is None:
            raise HTTPException(404, "project not found")
        registry.check_project(session, project, actor=operator())
        return views.project_view(project)


@router.get("/projects/{key}/stories")
def project_stories(key: str) -> dict:
    """SRS section 7 at the last checked commit, with the latest run of each story."""
    with session_scope() as session:
        project = session.scalar(select(Project).where(Project.key == key))
        if project is None:
            raise HTTPException(404, "project not found")
        return views.stories_view(session, project)


@router.post("/intents/draft")
def draft_intent(body: DraftRequest) -> dict:
    return get_team().draft_intent(body.text, body.title).model_dump()


@router.post("/runs", status_code=201)
def create_run(body: NewRun) -> dict:
    try:
        with session_scope() as session:
            project = session.scalar(select(Project).where(Project.key == body.project))
            if project is None:
                raise HTTPException(404, "project not found")
            run = services.create_run(session, project, body.intent or body.brief or body.architecture or body.discovery, actor=operator(),
                                      idempotency_key=body.idempotency_key)
            return views.run_summary(run, project.key)
    except services.DecisionError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get("/runs")
def list_runs(project: str | None = None, status: str | None = None) -> list[dict]:
    with session_scope() as session:
        query = select(Run, Project.key).join(Project, Project.id == Run.project_id).order_by(Run.created_at.desc())
        if project:
            query = query.where(Project.key == project)
        if status:
            query = query.where(Run.status == status)
        return [views.run_summary(run, key) for run, key in session.execute(query.limit(200))]


@router.get("/runs/{run_id}")
def get_run(run_id: uuid.UUID) -> dict:
    with session_scope() as session:
        run = session.get(Run, run_id)
        if run is None:
            raise HTTPException(404, "run not found")
        return views.run_detail(session, run)


@router.get("/runs/{run_id}/events")
def run_events(run_id: uuid.UUID) -> list[dict]:
    with session_scope() as session:
        return views.events_view(session, run_id)


@router.get("/runs/{run_id}/executions/{execution_id}/log")
def execution_log(run_id: uuid.UUID, execution_id: uuid.UUID, offset: int = 0) -> dict:
    """The job's live log from a byte offset; poll with the returned offset while status is queued or running."""
    with session_scope() as session:
        execution = session.get(Execution, execution_id)
        if execution is None or execution.run_id != run_id:
            raise HTTPException(404, "job not found")
        status = execution.status
    text, next_offset = joblog.read(run_id, execution_id, offset)
    return {"text": text, "offset": next_offset, "status": status}


class TextSource(Body):
    title: str = Field(min_length=1, max_length=120)
    text: str = Field(min_length=1)
    purpose: str = "requirements"  # or "minutes"


@router.post("/runs/{run_id}/sources")
async def upload_source(run_id: uuid.UUID, filename: str, request: Request, purpose: str = "requirements") -> dict:
    """The file is the raw request body (no multipart), named by the `filename` query parameter; `purpose=minutes`
    marks minutes of a meeting, whose decisions the Business Analyst reads."""
    data = await request.body()
    return _decide(run_id, lambda s, r: discovery.add_source(s, r, filename, data, actor=operator(), purpose=purpose))


@router.post("/runs/{run_id}/sources/text")
def paste_source(run_id: uuid.UUID, body: TextSource) -> dict:
    return _decide(run_id, lambda s, r: discovery.add_text_source(s, r, body.title, body.text, actor=operator(),
                                                                  purpose=body.purpose))


class Answer(Body):
    answer: str = Field(min_length=1)


class QuestionStatus(Body):
    status: str  # open, deferred, withdrawn
    reason: str = Field(min_length=1)


class Reassign(Body):
    role: str
    reason: str = ""


class DecisionVerdict(Body):
    accept: bool
    reason: str = ""


def _discovery(run_id: uuid.UUID, action) -> dict:
    """A question or decision of a discovery. Each one is an event; questions.md and decisions.md get their next
    version at the next extraction and at the baseline, not at every answer (17 deferrals made 21 versions)."""
    return _decide(run_id, action)


@router.post("/runs/{run_id}/questions/{question_id}/answer")
def answer_question(run_id: uuid.UUID, question_id: uuid.UUID, body: Answer) -> dict:
    return _discovery(run_id, lambda s, r: questions.answer(s, r, question_id, body.answer, actor=operator()))


@router.post("/runs/{run_id}/questions/{question_id}/status")
def question_status(run_id: uuid.UUID, question_id: uuid.UUID, body: QuestionStatus) -> dict:
    return _discovery(run_id, lambda s, r: questions.set_status(s, r, question_id, body.status, actor=operator(),
                                                                reason=body.reason))


@router.post("/runs/{run_id}/questions/{question_id}/reassign")
def reassign_question(run_id: uuid.UUID, question_id: uuid.UUID, body: Reassign) -> dict:
    return _discovery(run_id, lambda s, r: questions.reassign(s, r, question_id, body.role,
                                                              discovery.discovery_config(s, r), actor=operator(),
                                                              reason=body.reason))


class NewDecision(Body):
    text: str = Field(min_length=1)
    items: list[str] = []
    role: str = "po"
    effect: str = ""  # confirmed, out_of_scope or changed: applied to the items exactly
    new_text: str = ""


@router.post("/runs/{run_id}/decisions")
def record_decision(run_id: uuid.UUID, body: NewDecision) -> dict:
    """A decision taken directly (for example REQ-002 out of scope); Extract again applies it to the inventory."""
    return _discovery(run_id, lambda s, r: questions.record(s, r, body.text, body.items, body.role,
                                                            discovery.discovery_config(s, r), actor=operator(),
                                                            effect=body.effect, new_text=body.new_text))


@router.post("/runs/{run_id}/decisions/{number}")
def decide_decision(run_id: uuid.UUID, number: int, body: DecisionVerdict) -> dict:
    return _discovery(run_id, lambda s, r: questions.decide(s, r, number, body.accept, actor=operator(),
                                                            reason=body.reason))


class BaselineScope(Body):
    scope: str = Field(min_length=1)


class BaselineApproval(Body):
    document_id: uuid.UUID
    checked: list[str] = []


@router.post("/runs/{run_id}/baseline")
def prepare_baseline(run_id: uuid.UUID, body: BaselineScope) -> dict:
    """The Product Owner states the phase's scope; the baseline waits at its gate with the checklist."""
    return _decide(run_id, lambda s, r: discovery.prepare_baseline(s, r, body.scope, actor=operator()))


@router.post("/runs/{run_id}/baseline/approve")
def approve_baseline(run_id: uuid.UUID, body: BaselineApproval) -> dict:
    return _decide(run_id, lambda s, r: discovery.approve_baseline(s, r, body.document_id, body.checked, actor=operator()))


@router.post("/runs/{run_id}/baseline/reject")
def reject_baseline(run_id: uuid.UUID, body: Reason) -> dict:
    return _decide(run_id, lambda s, r: discovery.reject_baseline(s, r, actor=operator(), reason=body.reason))


class OptionChoice(Body):
    document_id: uuid.UUID
    option: str
    reason: str = ""
    checked: list[str] = []


@router.post("/runs/{run_id}/options/choose")
def choose_option(run_id: uuid.UUID, body: OptionChoice) -> dict:
    return _decide(run_id, lambda s, r: services.choose_option(s, r, body.document_id, body.option, actor=operator(),
                                                               reason=body.reason, checked=body.checked))


@router.post("/runs/{run_id}/options/redo")
def redo_options(run_id: uuid.UUID, body: Reason) -> dict:
    return _decide(run_id, lambda s, r: services.redo_options(s, r, actor=operator(), reason=body.reason))


@router.get("/runs/{run_id}/workshop.md", response_class=PlainTextResponse)
def workshop(run_id: uuid.UUID, roles: str = "") -> str:
    """The open questions of the roles that attend a meeting (comma-separated role ids; empty = every role)."""
    with session_scope() as session:
        run = services.get_run(session, run_id)
        config = discovery.discovery_config(session, run)
        return questions.workshop(questions.questions(session, run.id), config,
                                  [r for r in roles.split(",") if r], run.title.removeprefix("Discovery: "))


@router.post("/runs/{run_id}/extract")
def extract(run_id: uuid.UUID, again: bool = False) -> dict:
    """The Business Analyst reads what is new and writes the next requirement inventory; `again=true` checks the
    inventory once more with nothing new to read."""
    return _decide(run_id, lambda s, r: discovery.start_extraction(s, r, actor=operator(), again=again))


@router.post("/runs/{run_id}/sources/{source_id}/remove")
def remove_source(run_id: uuid.UUID, source_id: uuid.UUID, body: Reason) -> dict:
    return _decide(run_id, lambda s, r: discovery.remove_source(s, r, source_id, actor=operator(), reason=body.reason))


def _source(run_id: uuid.UUID, source_id: uuid.UUID) -> Source:
    with session_scope() as session:
        source = session.get(Source, source_id)
        if source is None or source.run_id != run_id:
            raise HTTPException(404, "source not found")
        session.expunge(source)
        return source


@router.get("/runs/{run_id}/sources/{source_id}/file")
def source_file(run_id: uuid.UUID, source_id: uuid.UUID) -> FileResponse:
    source = _source(run_id, source_id)
    return FileResponse(source.path, filename=source.filename, content_disposition_type="inline")


@router.get("/runs/{run_id}/sources/{source_id}/text")
def source_text(run_id: uuid.UUID, source_id: uuid.UUID) -> dict:
    """The text view with locations of a DOCX, XLSX, PPTX or text source."""
    source = _source(run_id, source_id)
    path = Path(source.text_path or (source.path if source.kind == "text" else ""))
    if not source.text_path and source.kind != "text":
        raise HTTPException(404, f"{source.kind} sources are read as they are; open the file instead")
    return {"filename": source.filename, "text": path.read_text()}


@router.get("/inbox")
def inbox() -> list[dict]:
    with session_scope() as session:
        return views.inbox(session)


@router.post("/runs/{run_id}/answers")
def answers(run_id: uuid.UUID, body: Answers) -> dict:
    return _decide(run_id, lambda s, r: services.answer_questions(s, r, body.answers, actor=operator()))


@router.post("/runs/{run_id}/approve-spec")
def approve_spec(run_id: uuid.UUID, body: DocumentChoice) -> dict:
    return _decide(run_id, lambda s, r: services.approve_spec(s, r, body.document_id, body.sha256, actor=operator(),
                                                              checked=body.checked))


@router.post("/runs/{run_id}/revise-spec")
def revise_spec(run_id: uuid.UUID, body: Revision) -> dict:
    return _decide(run_id, lambda s, r: services.revise_spec(s, r, body.content, actor=operator(), reason=body.reason))


@router.post("/runs/{run_id}/reject-spec")
def reject_spec(run_id: uuid.UUID, body: Rejection) -> dict:
    return _decide(run_id, lambda s, r: services.reject_spec(s, r, actor=operator(), reason=body.reason,
                                                             next_step=body.next))


@router.post("/runs/{run_id}/approve-plan")
def approve_plan(run_id: uuid.UUID, body: PlanChoice) -> dict:
    return _decide(run_id, lambda s, r: services.approve_plan(s, r, body.plan_id, body.sha256, actor=operator(),
                                                                       checked=body.checked))


@router.post("/runs/{run_id}/revise-plan")
def revise_plan(run_id: uuid.UUID, body: PlanRevision) -> dict:
    return _decide(run_id, lambda s, r: services.revise_plan(s, r, body.plan, body.test_plan, actor=operator(),
                                                             reason=body.reason))


@router.post("/runs/{run_id}/reject-plan")
def reject_plan(run_id: uuid.UUID, body: Rejection) -> dict:
    return _decide(run_id, lambda s, r: services.reject_plan(s, r, actor=operator(), reason=body.reason,
                                                             next_step=body.next))


@router.post("/runs/{run_id}/approve-tests")
def approve_tests(run_id: uuid.UUID, body: DocumentChoice) -> dict:
    return _decide(run_id, lambda s, r: services.approve_tests(s, r, body.document_id, body.sha256, actor=operator(),
                                                               checked=body.checked))


@router.post("/runs/{run_id}/revise-tests")
def revise_tests(run_id: uuid.UUID, body: TestsRevision) -> dict:
    return _decide(run_id, lambda s, r: services.revise_tests(s, r, body.test_plan, actor=operator(), reason=body.reason))


@router.post("/runs/{run_id}/reject-tests")
def reject_tests(run_id: uuid.UUID, body: Rejection) -> dict:
    return _decide(run_id, lambda s, r: services.reject_tests(s, r, actor=operator(), reason=body.reason,
                                                              next_step=body.next))


@router.post("/runs/{run_id}/start-stage")
def start_stage(run_id: uuid.UUID, body: StageStart) -> dict:
    return _decide(run_id, lambda s, r: services.start_stage(s, r, body.stage, actor=operator(),
                                                             acting_role=STAGE_STARTER[body.stage].value))


@router.post("/runs/{run_id}/resume")
def resume(run_id: uuid.UUID, body: Resume) -> dict:
    return _decide(run_id, lambda s, r: services.resume(s, r, actor=operator(), decision=body.decision,
                                                        reason=body.reason))


@router.post("/runs/{run_id}/cancel")
def cancel(run_id: uuid.UUID, body: Reason) -> dict:
    return _decide(run_id, lambda s, r: services.cancel(s, r, actor=operator(), reason=body.reason))


@router.post("/runs/{run_id}/pr-decision")
def pr_decision(run_id: uuid.UUID, body: PrDecision) -> dict:
    return _decide(run_id, lambda s, r: services.pr_decision(s, r, actor=operator(), decision=body.decision,
                                                             reason=body.reason, checked=body.checked))


@router.post("/runs/{run_id}/classify-failure")
def classify(run_id: uuid.UUID, body: Classification) -> dict:
    return _decide(run_id, lambda s, r: services.classify(s, r, actor=operator(), category=body.category,
                                                          stage=body.stage, role=body.role, note=body.note))


class NewEval(Body):
    suite: str
    engines: dict[str, str] | None = None  # thinking role -> qwen3, claude-readonly or codex-readonly


@router.get("/eval-suites")
def eval_suites() -> list[dict]:
    suites = []
    for name in evals.list_suites():
        suite = evals.load_suite(name)
        suites.append({"name": suite.name, "project": suite.project, "base_commit": suite.base_commit,
                       "cases": [case.id for case in suite.cases]})
    return suites


@router.get("/evals")
def list_evals() -> list[dict]:
    with session_scope() as session:
        rows = session.scalars(select(EvalRun).order_by(EvalRun.created_at.desc()).limit(50))
        return [evals.eval_view(session, e, with_cases=False) for e in rows]


@router.post("/evals", status_code=201)
def start_eval(body: NewEval) -> dict:
    try:
        with session_scope() as session:
            evaluation = evals.start_eval(session, body.suite, actor=operator(), engines=body.engines)
            return evals.eval_view(session, evaluation)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except services.DecisionError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get("/evals/{eval_id}")
def get_eval(eval_id: uuid.UUID) -> dict:
    with session_scope() as session:
        evaluation = session.get(EvalRun, eval_id)
        if evaluation is None:
            raise HTTPException(404, "eval not found")
        return evals.eval_view(session, evaluation)


@router.post("/webhooks/github")
async def github_webhook(request: Request) -> dict:
    """Signed GitHub events (pull_request, checks, reviews). Without a secret the worker polls instead."""
    from controller.app.doing.github import verify_signature
    secret = get_settings().github_webhook_secret
    if not secret:
        raise HTTPException(404, "webhook disabled: set GITHUB_WEBHOOK_SECRET in .env")
    body = await request.body()
    if not verify_signature(secret, body, request.headers.get("X-Hub-Signature-256", "")):
        raise HTTPException(401, "bad signature")
    delivery = request.headers.get("X-GitHub-Delivery", "")
    if not delivery:
        raise HTTPException(400, "missing X-GitHub-Delivery")
    with session_scope() as session:
        return {"status": services.github_event(session, delivery, request.headers.get("X-GitHub-Event", ""))}


@router.get("/reports")
def reports() -> dict:
    """Team metrics per role and engine, from normal runs; engine comparisons come from evals."""
    from controller.app import reports as team_reports
    with session_scope() as session:
        return team_reports.team_report(session)


@router.get("/runs/{run_id}/diff")
def run_diff(run_id: uuid.UUID) -> dict:
    """The run's code change from its base commit (sources first, then tests, without the .sdlc thread)."""
    from pathlib import Path
    from controller.app.coordinator import artifact
    from controller.app.doing import changes
    from controller.app.projects.config import EffectivePolicy
    with session_scope() as session:
        run = session.get(Run, run_id)
        if run is None:
            raise HTTPException(404, "run not found")
        if not run.worktree_path or not run.base_commit or not Path(run.worktree_path).exists():
            return {"diff": "", "files": []}
        policy = artifact(session, run.id, "effective_policy")
        test_paths = EffectivePolicy.model_validate_json(policy).test_paths if policy else []
        diff = changes.run_diff(Path(run.worktree_path), run.base_commit, test_paths, 400_000)
    files = [line.split(" b/", 1)[1] for line in diff.splitlines() if line.startswith("diff --git ") and " b/" in line]
    return {"diff": diff, "files": files}
