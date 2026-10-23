"""Team API used by the web control room. Every decision is made as SDLC_OPERATOR."""

import logging
from pathlib import Path
from typing import Literal
import uuid

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select

from controller.app import evals, joblog, services
from controller.app.api import views
from controller.app.config import get_settings
from controller.app.db import session_scope
from controller.app.discovery import service as discovery
from controller.app.discovery import questions
from controller.app.discovery.service import DiscoveryRequest
from controller.app.foundation import FoundationRequest
from controller.app.models import Document, EvalRun, Execution, Project, Run, Source
from controller.app.projects import registry
from controller.app.project_docs import ArchitectureBrief, ProjectBrief
from controller.app.schemas import Intent
from controller.app.states import STAGE_STARTER, HumanRole
from controller.app.team import get_team

log = logging.getLogger("sdlc.web")
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
    foundation: FoundationRequest | None = None  # after the SDS: onboarding files and the walking skeleton
    idempotency_key: str = Field(min_length=8, max_length=128)

    @model_validator(mode="after")
    def _one_input(self) -> "NewRun":
        if sum(x is not None for x in (self.intent, self.brief, self.architecture, self.discovery, self.foundation)) != 1:
            raise ValueError("send exactly one of intent (a feature), brief (the SRS), architecture (the SDS), "
                             "discovery or foundation")
        return self


class DocumentApproval(Body):
    document_id: uuid.UUID
    checked: list[str] = []


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
    wait_for: list[str] = []  # open runs that change the same files: this run codes after they finish


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
    decision: Literal["resume", "replan", "cancel", "amend_tests", "keep_tests", "already_built",
                      "defects_as_written", "defects_revised", "defects_retest", "allow_files"]
    reason: str
    files: list[str] = []  # allow_files: what the disputed task may change besides its plan's files


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


class ClientError(Body):
    message: str = Field(max_length=2000)
    stack: str = Field(default="", max_length=8000)
    where: str = Field(default="", max_length=32)
    url: str = Field(default="", max_length=500)


@router.post("/client-errors", status_code=204)
def client_error(body: ClientError) -> None:
    """A crash of the control room in the browser, written to the API log for diagnosis."""
    log.error("control room error (%s) at %s: %s\n%s", body.where, body.url, body.message, body.stack)


@router.get("/meta")
def meta() -> dict:
    return {"operator": operator(), "team_mode": get_settings().sdlc_team_mode,
            "doing_mode": get_settings().sdlc_doing_mode, "engines": get_settings().default_engines,
            "discovery_engine": get_settings().sdlc_discovery_engine or get_settings().default_engines["business_analyst"],
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


def _story_context(key: str, story: str) -> dict:
    from controller.app.outcomes import story_context, suggest
    from controller.app.project_docs import SDS_PATH, SRS_PATH
    with session_scope() as session:
        project = session.scalar(select(Project).where(Project.key == key))
        if project is None:
            raise HTTPException(404, "project not found")
        mirror = registry.mirror_for(project)
        commit = project.checked_commit
        srs = mirror.read_file(commit, SRS_PATH) if commit else None
        sds = (mirror.read_file(commit, SDS_PATH) or "") if commit else ""
    if not srs or story not in srs:
        raise HTTPException(404, f"{story} is not a user story of {key}'s SRS")
    context = story_context(srs, sds, story)
    return {**context, "suggestions": suggest(context)}


@router.get("/projects/{key}/stories/{story}/outcomes")
def story_outcomes(key: str, story: str) -> dict:
    """Desired outcomes drafted from the SRS and SDS of the story, each with its source; no model is asked."""
    return _story_context(key, story)


@router.post("/projects/{key}/stories/{story}/outcomes/ai")
def story_outcomes_ai(key: str, story: str) -> dict:
    """The same sources given to the thinking engine, which writes four to eight outcomes in a Product Owner's words."""
    context = _story_context(key, story)
    answer = get_team().suggest_outcomes(context)
    return {"suggestions": [o.model_dump() for o in answer.outcomes], "notes": answer.notes}


class BacklogEdit(Body):
    items: list[dict]
    notes: str = ""
    version: int


class BacklogFeedback(Body):
    feedback: str = ""


class BacklogApproval(Body):
    version: int
    reason: str = ""
    auto_start: bool | None = None  # False: approve and open nothing on its own


class AutoStart(Body):
    enabled: bool | None  # None: follow delivery.auto_start of project.yaml


def _backlog(key: str, action) -> dict:
    """One backlog action in a transaction, then the backlog as the page shows it."""
    from controller.app import backlog
    try:
        with session_scope() as session:
            project = session.scalar(select(Project).where(Project.key == key))
            if project is None:
                raise HTTPException(404, "project not found")
            result = action(session, project)
            session.flush()
            return result if isinstance(result, dict) else backlog.view(session, project)
    except (backlog.BacklogError, services.DecisionError) as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get("/projects/{key}/backlog")
def project_backlog(key: str) -> dict:
    """The user stories in delivery order, with their dependencies and each one's state."""
    from controller.app import backlog
    return _backlog(key, lambda s, p: backlog.view(s, p))


@router.post("/projects/{key}/backlog/propose")
def propose_backlog(key: str, body: BacklogFeedback) -> dict:
    """The Tech Lead AI orders every story from the SRS and SDS; the result is a draft for the Tech Lead."""
    from controller.app import backlog
    context = _backlog(key, lambda s, p: backlog.proposal_context(s, p, body.feedback))
    answer = get_team().order_backlog(context)
    return _backlog(key, lambda s, p: backlog.store_proposal(s, p, answer, actor=operator(), feedback=body.feedback))


@router.put("/projects/{key}/backlog")
def edit_backlog(key: str, body: BacklogEdit) -> dict:
    from controller.app import backlog
    return _backlog(key, lambda s, p: backlog.save(s, p, body.items, body.notes, actor=operator(), version=body.version))


@router.post("/projects/{key}/backlog/approve")
def approve_backlog(key: str, body: BacklogApproval) -> dict:
    from controller.app import backlog
    return _backlog(key, lambda s, p: backlog.approve(s, p, actor=operator(), version=body.version, reason=body.reason,
                                                      auto_start=body.auto_start))


@router.post("/projects/{key}/backlog/auto-start")
def backlog_auto_start(key: str, body: AutoStart) -> dict:
    """The Tech Lead's switch for opening stories on their own, over delivery.auto_start of project.yaml."""
    from controller.app import backlog
    return _backlog(key, lambda s, p: backlog.set_auto_start(s, p, body.enabled, actor=operator()))


@router.get("/projects/{key}/backlog/{story}")
def backlog_story(key: str, story: str) -> dict:
    """One story opened in the backlog: its SRS and SDS sections, requirements, APIs, runs and accepted spec."""
    from controller.app import backlog
    return _backlog(key, lambda s, p: backlog.story_detail(s, p, story))


@router.post("/projects/{key}/backlog/{story}/start", status_code=201)
def start_story(key: str, story: str) -> dict:
    """Open the run of a ready story, with its outcomes drafted from the SRS and SDS."""
    from controller.app import backlog
    return _backlog(key, lambda s, p: views.run_summary(backlog.start(s, p, story, actor=operator()), p.key))


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
            run = services.create_run(session, project, body.intent or body.brief or body.architecture or body.discovery
                                      or body.foundation, actor=operator(),
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


@router.get("/runs/{run_id}/executions/{execution_id}/prompts")
def execution_prompts(run_id: uuid.UUID, execution_id: uuid.UUID, name: str = "") -> dict:
    """The prompts the job's calls sent, in order; with `name`, the text of one."""
    with session_scope() as session:
        execution = session.get(Execution, execution_id)
        if execution is None or execution.run_id != run_id:
            raise HTTPException(404, "job not found")
        version = execution.prompt_version
    if name:
        text = joblog.read_prompt(run_id, execution_id, name)
        if text is None:
            raise HTTPException(404, "prompt not found")
        return {"name": name, "text": text}
    return {"prompts": joblog.list_prompts(run_id, execution_id), "prompt_version": version}


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
    adjustments: str = ""  # the Solution Architect's changes to the chosen option
    hosting: str = ""  # PA1-H2: the hosting variant of the option; empty takes the recommended one
    hosting_reason: str = ""  # why this hosting; required when the brief does not state the deployment


@router.post("/runs/{run_id}/options/choose")
def choose_option(run_id: uuid.UUID, body: OptionChoice) -> dict:
    return _decide(run_id, lambda s, r: services.choose_option(s, r, body.document_id, body.option, actor=operator(),
                                                               reason=body.reason, checked=body.checked,
                                                               adjustments=body.adjustments, hosting=body.hosting,
                                                               hosting_reason=body.hosting_reason))


class UiChoice(Body):
    document_id: uuid.UUID
    concept: str
    reason: str = ""
    adjustments: str = ""
    checked: list[str] = []


@router.post("/runs/{run_id}/ui/choose")
def choose_ui(run_id: uuid.UUID, body: UiChoice) -> dict:
    return _decide(run_id, lambda s, r: services.choose_ui(s, r, body.document_id, body.concept, actor=operator(),
                                                           reason=body.reason, checked=body.checked,
                                                           adjustments=body.adjustments))


@router.post("/runs/{run_id}/ui/redo")
def redo_ui(run_id: uuid.UUID, body: Reason) -> dict:
    return _decide(run_id, lambda s, r: services.redo_ui(s, r, actor=operator(), reason=body.reason))


class UiPreview(Body):
    preset: str
    colors: list[str] = []
    fonts: list[str] = []
    product: str = ""
    project: str = ""  # its SRS features become the menu
    logo: str = ""  # a data: URI of the logo being uploaded
    device: Literal["desktop", "tablet", "mobile", "kiosk"] = "desktop"
    screen: str = "overview"
    accessibility: Literal["wcag-aa", "wcag-aaa", "none"] = "wcag-aa"


@router.get("/ui/presets")
def ui_presets() -> list[dict]:
    """The platform's default UI concepts (ui-presets/)."""
    from controller.app.ui import presets
    return [{k: p[k] for k in ("id", "name", "best_for", "layout", "density") if k in p} for p in presets()]


@router.post("/ui/preview")
def ui_preview(body: UiPreview) -> dict:
    """A default concept with the brand typed so far applied, on sample screens, for the SDS request form; no model is
    asked. The HTML has no script and no external request; the page shows it in a sandboxed frame."""
    from controller.app.project_docs import SRS_PATH
    from controller.app.ui import HEX, PREVIEW_SCREENS, check, presets, preview_concept, render
    preset = next((p for p in presets() if p["id"] == body.preset), None)
    if preset is None:
        raise HTTPException(404, f"no preset {body.preset}")
    colors = [c.strip() for c in body.colors if HEX.fullmatch(c.strip())]
    navigation: list[str] = []
    if body.project:
        with session_scope() as session:
            project = session.scalar(select(Project).where(Project.key == body.project))
            if project is not None and project.checked_commit:
                from controller.app.project_docs import srs_features
                srs = registry.mirror_for(project).read_file(project.checked_commit, SRS_PATH) or ""
                navigation = [f.name for f in srs_features(srs)]
    concept = preview_concept(preset, colors, [f.strip() for f in body.fonts if f.strip()], navigation)
    screen = next((s for s in concept.screens if s.name == body.screen), concept.screens[0])
    logo = body.logo if body.logo.startswith("data:image/") else ""
    return {"html": render(concept, screen, body.device, body.product, logo), "screens": list(PREVIEW_SCREENS),
            "checks": check(concept, body.accessibility), "ignored_colors": [c for c in body.colors if c.strip() not in colors]}


@router.get("/runs/{run_id}/ui/{version}/{concept}/{screen}", response_class=HTMLResponse)
def ui_prototype(run_id: uuid.UUID, version: int, concept: str, screen: str, device: str = "desktop") -> HTMLResponse:
    """One prototype screen of a UI concept, rendered by the controller; no script, shown in a sandboxed frame."""
    from controller.app.ui import UiConcepts, render, slug
    with session_scope() as session:
        document = session.scalar(select(Document).where(Document.run_id == run_id, Document.kind == "ui_concepts",
                                                         Document.version == version))
        if document is None:
            raise HTTPException(404, "no such UI concepts version")
        run = session.get(Run, run_id)
        concepts = UiConcepts.model_validate({k: v for k, v in document.data.items() if k not in ("checks", "brief")})
        title = run.title.removeprefix("SDS: ") if run else ""
    found = next((c for c in concepts.concepts if c.id == concept), None)
    page = next((s for s in (found.screens if found else []) if slug(s.name) == screen), None)
    if page is None:
        raise HTTPException(404, "no such screen")
    return HTMLResponse(render(found, page, device, title),
                        headers={"Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; img-src data:"})


class AcceptanceDecision(Body):
    decision: Literal["accept", "reject", "retest"]
    reason: str = ""
    document_id: uuid.UUID | None = None
    checked: list[str] = []


@router.post("/runs/{run_id}/acceptance")
def acceptance(run_id: uuid.UUID, body: AcceptanceDecision) -> dict:
    return _decide(run_id, lambda s, r: services.decide_acceptance(s, r, actor=operator(), decision=body.decision,
                                                                   reason=body.reason, document_id=body.document_id,
                                                                   checked=body.checked))


@router.get("/runs/{run_id}/evidence/{path:path}")
def evidence(run_id: uuid.UUID, path: str) -> FileResponse:
    """A screenshot, video or trace the acceptance tests kept, from .data/evidence/<run>/."""
    from controller.app.config import DATA_DIR
    root = (DATA_DIR / "evidence" / str(run_id)).resolve()
    target = (root / path).resolve()
    if root not in target.parents or not target.is_file():
        raise HTTPException(404, "no such evidence")
    return FileResponse(target)


class PreviewAction(Body):
    action: Literal["start", "stop"]


@router.post("/runs/{run_id}/preview")
def preview(run_id: uuid.UUID, body: PreviewAction) -> dict:
    return _decide(run_id, lambda s, r: services.preview(s, r, actor=operator(), start=body.action == "start"))


@router.post("/runs/{run_id}/advice")
def advice(run_id: uuid.UUID) -> dict:
    return _decide(run_id, lambda s, r: services.request_advice(s, r, actor=operator()))


@router.post("/runs/{run_id}/onboarding/approve")
def approve_onboarding(run_id: uuid.UUID, body: DocumentApproval) -> dict:
    return _decide(run_id, lambda s, r: services.approve_onboarding(s, r, body.document_id, actor=operator(),
                                                                    checked=body.checked))


@router.post("/runs/{run_id}/onboarding/reopen")
def reopen_onboarding(run_id: uuid.UUID, body: Reason) -> dict:
    return _decide(run_id, lambda s, r: services.reopen_onboarding(s, r, actor=operator(), reason=body.reason))


class OnboardingEdit(Body):
    files: dict[str, str]
    reason: str = ""
    from_version: int | None = None


@router.post("/runs/{run_id}/onboarding/edit")
def edit_onboarding(run_id: uuid.UUID, body: OnboardingEdit) -> dict:
    return _decide(run_id, lambda s, r: services.edit_onboarding(s, r, actor=operator(), files=body.files,
                                                                 reason=body.reason, from_version=body.from_version))


@router.post("/runs/{run_id}/onboarding/redo")
def redo_onboarding(run_id: uuid.UUID, body: Reason) -> dict:
    return _decide(run_id, lambda s, r: services.redo_onboarding(s, r, actor=operator(), reason=body.reason))


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


class ExtractRound(Body):
    beyond_limit: str = ""  # the Product Owner's reason for a round past discovery.limits.max_rounds


@router.post("/runs/{run_id}/extract")
def extract(run_id: uuid.UUID, body: ExtractRound | None = None, again: bool = False) -> dict:
    """The Business Analyst reads what is new and writes the next requirement inventory; `again=true` checks the
    inventory once more with nothing new to read."""
    reason = body.beyond_limit if body else ""
    return _decide(run_id, lambda s, r: discovery.start_extraction(s, r, actor=operator(), again=again,
                                                                   beyond_limit=reason))


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
                                                                       checked=body.checked, wait_for=body.wait_for))


class PlanAmendment(Body):
    task: str
    criterion: str
    reason: str = ""


@router.post("/runs/{run_id}/amend-plan")
def amend_plan(run_id: uuid.UUID, body: PlanAmendment) -> dict:
    """At the test cases gate: add one criterion to one task of the approved plan, as the analysis suggests."""
    return _decide(run_id, lambda s, r: services.amend_plan(s, r, task_id=body.task, criterion=body.criterion,
                                                            actor=operator(), reason=body.reason))


@router.post("/runs/{run_id}/stop-waiting")
def stop_waiting(run_id: uuid.UUID, body: Reason) -> dict:
    """A run that waits for other runs to finish codes now."""
    return _decide(run_id, lambda s, r: services.stop_waiting(s, r, actor=operator(), reason=body.reason))


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
                                                        reason=body.reason, files=body.files))


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


@router.get("/runs/{run_id}/documents/{kind}/diff")
def document_diff(run_id: uuid.UUID, kind: str, base: int, head: int) -> dict:
    """What changed in a run document between two of its versions, as a unified diff."""
    from controller.app.documents import document_diff as diff
    from controller.app.models import Document
    with session_scope() as session:
        docs = {d.version: d for d in session.scalars(select(Document).where(
            Document.run_id == run_id, Document.kind == kind, Document.version.in_((base, head))))}
        if base not in docs or head not in docs:
            raise HTTPException(404, "document version not found")
        return {"diff": diff(docs[base].content, docs[head].content, f"{kind} v{base}", f"{kind} v{head}")}


@router.get("/runs/{run_id}/diff")
def run_diff(run_id: uuid.UUID) -> dict:
    """The run's code change from its base commit (sources first, then tests, without the .sdlc thread)."""
    from pathlib import Path
    from controller.app.coordinator import artifact, diff_base
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
        diff = changes.run_diff(Path(run.worktree_path), diff_base(session, run), test_paths, 400_000)
    files = [line.split(" b/", 1)[1] for line in diff.splitlines() if line.startswith("diff --git ") and " b/" in line]
    return {"diff": diff, "files": files}
