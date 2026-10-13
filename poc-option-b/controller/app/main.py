import hmac
import json
from uuid import UUID
from uuid import uuid4

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pathlib import Path
from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from controller.app.config import Settings, get_settings
from controller.app.integrations.github_webhook import (
    WebhookAuthError, WebhookInvalidError, apply_github_webhook,
)
from controller.app.db import get_engine, get_session
from controller.app.models import Artifact, Event, Plan, PullRequest, Run, Task
from controller.app.schemas import FailureClassification, PlanDecision, PlanRevision, RunCreate, VersionedDecision
from controller.app.services import (
    approve_plan, approve_pr, cancel_run, classify_failure, create_run, get_run, reject_plan, revise_plan,
)
from controller.app.state_machine import ConflictError, NotFoundError


app = FastAPI(title="AI-Native SDLC Controller", version="0.1.0")
WEB_ROOT = Path(__file__).resolve().parents[1] / "web"
PILOT_ROOT = Path(__file__).resolve().parents[2] / "fixtures" / "task-board"
app.mount("/static", StaticFiles(directory=WEB_ROOT), name="static")


@app.get("/", include_in_schema=False)
def dashboard() -> FileResponse:
    return FileResponse(WEB_ROOT / "index.html", headers={
        "Cache-Control": "no-store",
        "Content-Security-Policy": "default-src 'self'; connect-src 'self'; script-src 'self'; style-src 'self'; object-src 'none'; base-uri 'none'",
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "no-referrer",
    })


@app.exception_handler(ConflictError)
def conflict_handler(_request, exc: ConflictError):
    from fastapi.responses import JSONResponse

    return JSONResponse(status_code=409, content={"detail": str(exc)})


@app.exception_handler(NotFoundError)
def not_found_handler(_request, exc: NotFoundError):
    from fastapi.responses import JSONResponse

    return JSONResponse(status_code=404, content={"detail": str(exc)})


def require_controller_token(
    token: str | None = Header(default=None, alias="X-Controller-Token"),
    settings: Settings = Depends(get_settings),
) -> None:
    if token is None or not hmac.compare_digest(token, settings.controller_token):
        raise HTTPException(status_code=401, detail="unauthorized")


def run_response(run) -> dict:
    return {
        "id": str(run.id), "repo_id": run.repo_id, "base_commit": run.base_commit,
        "executor": run.executor,
        "status": run.status, "version": run.version,
        "failure_category": run.failure_category, "failure_stage": run.failure_stage,
        "failure_note": run.failure_note,
    }


@app.get("/api/runs", dependencies=[Depends(require_controller_token)])
def api_list_runs(limit: int = 50, session: Session = Depends(get_session)) -> list[dict]:
    if limit < 1 or limit > 100:
        raise HTTPException(status_code=422, detail="limit must be between 1 and 100")
    runs = session.scalars(select(Run).order_by(Run.created_at.desc(), Run.id.desc()).limit(limit)).all()
    return [{**run_response(run), "created_at": run.created_at.isoformat()} for run in runs]


@app.get("/api/pilot/features", dependencies=[Depends(require_controller_token)])
def api_pilot_features() -> list[dict[str, str]]:
    manifest = json.loads((PILOT_ROOT / "benchmark.json").read_text())
    return [{"id": item["id"], "requirement": (PILOT_ROOT / item["requirement_file"]).read_text()}
            for item in manifest["tasks"]]


@app.post("/api/pilot/runs/{feature_id}", dependencies=[Depends(require_controller_token)], status_code=201)
def api_create_pilot_run(
    feature_id: str, session: Session = Depends(get_session), settings: Settings = Depends(get_settings),
) -> dict:
    manifest = json.loads((PILOT_ROOT / "benchmark.json").read_text())
    feature = next((item for item in manifest["tasks"] if item["id"] == feature_id), None)
    if feature is None:
        raise HTTPException(status_code=404, detail="pilot feature not found")
    request = RunCreate(
        repo_id=manifest["repository"].removeprefix("https://github.com/").removesuffix(".git"),
        base_commit=manifest["base_commit"],
        requirement=(PILOT_ROOT / feature["requirement_file"]).read_text(),
        idempotency_key=f"pilot:{feature_id}:{uuid4()}",
    )
    run = create_run(session, request, settings.repo_allowlist)
    session.commit()
    return run_response(run)


@app.get("/health/live")
def live() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/health/ready")
def ready() -> dict[str, str]:
    try:
        with get_engine().connect() as connection:
            connection.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        raise HTTPException(status_code=503, detail="database unavailable") from exc
    return {"status": "ok"}


@app.post("/api/runs", dependencies=[Depends(require_controller_token)], status_code=201)
def api_create_run(
    request: RunCreate,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> dict:
    run = create_run(session, request, settings.repo_allowlist)
    session.commit()
    return run_response(run)


@app.get("/api/runs/{run_id}", dependencies=[Depends(require_controller_token)])
def api_get_run(run_id: UUID, session: Session = Depends(get_session)) -> dict:
    run = get_run(session, run_id)
    tasks = session.scalars(select(Task).where(Task.run_id == run_id).order_by(Task.external_task_id)).all()
    plans = session.scalars(select(Plan).where(Plan.run_id == run_id).order_by(Plan.version)).all()
    artifacts = session.scalars(select(Artifact).where(Artifact.run_id == run_id).order_by(Artifact.created_at)).all()
    pull_request = session.scalar(select(PullRequest).where(PullRequest.run_id == run_id))
    return {
        **run_response(run),
        "requirement": run.requirement,
        "created_at": run.created_at.isoformat(),
        "tasks": [
            {"id": str(task.id), "external_task_id": task.external_task_id,
             "status": task.status, "attempt_count": task.attempt_count, "executor": task.executor}
            for task in tasks
        ],
        "plans": [
            {"id": str(plan.id), "version": plan.version, "sha256": plan.sha256, "body": plan.body}
            for plan in plans
        ],
        "artifacts": [
            {"id": str(artifact.id), "kind": artifact.kind,
             "storage_path": artifact.storage_path, "checksum": artifact.checksum}
            for artifact in artifacts
        ],
        "pull_request": None if pull_request is None else {
            "number": pull_request.number, "url": pull_request.url,
            "head_sha": pull_request.head_sha, "ci_status": pull_request.ci_status,
        },
    }


@app.get("/api/runs/{run_id}/events", dependencies=[Depends(require_controller_token)])
def api_get_events(run_id: UUID, session: Session = Depends(get_session)) -> list[dict]:
    get_run(session, run_id)
    records = session.scalars(select(Event).where(Event.run_id == run_id).order_by(Event.created_at, Event.id)).all()
    return [
        {"id": str(item.id), "event_type": item.event_type,
         "payload": item.payload_jsonb, "created_at": item.created_at.isoformat()}
        for item in records
    ]


@app.post("/api/runs/{run_id}/approve-plan", dependencies=[Depends(require_controller_token)])
def api_approve_plan(run_id: UUID, decision: PlanDecision, session: Session = Depends(get_session)) -> dict:
    run = approve_plan(session, run_id, decision)
    session.commit()
    return run_response(run)


@app.post("/api/runs/{run_id}/revise-plan", dependencies=[Depends(require_controller_token)])
def api_revise_plan(run_id: UUID, revision: PlanRevision, session: Session = Depends(get_session)) -> dict:
    plan = revise_plan(session, run_id, revision)
    session.commit()
    run = get_run(session, run_id)
    return {**run_response(run), "plan_id": str(plan.id), "plan_version": plan.version, "plan_sha256": plan.sha256}


@app.post("/api/runs/{run_id}/reject-plan", dependencies=[Depends(require_controller_token)])
def api_reject_plan(run_id: UUID, decision: PlanDecision, session: Session = Depends(get_session)) -> dict:
    run = reject_plan(session, run_id, decision)
    session.commit()
    return run_response(run)


@app.post("/api/runs/{run_id}/cancel", dependencies=[Depends(require_controller_token)])
def api_cancel_run(run_id: UUID, decision: VersionedDecision, session: Session = Depends(get_session)) -> dict:
    run = cancel_run(session, run_id, decision)
    session.commit()
    return run_response(run)


@app.post("/api/runs/{run_id}/approve-pr", dependencies=[Depends(require_controller_token)])
def api_approve_pr(run_id: UUID, decision: VersionedDecision, session: Session = Depends(get_session)) -> dict:
    run = approve_pr(session, run_id, decision)
    session.commit()
    return run_response(run)


@app.post("/api/runs/{run_id}/classify-failure", dependencies=[Depends(require_controller_token)])
def api_classify_failure(
    run_id: UUID, classification: FailureClassification, session: Session = Depends(get_session)
) -> dict:
    run = classify_failure(session, run_id, classification)
    session.commit()
    return run_response(run)


@app.post("/api/webhooks/github")
async def api_github_webhook(
    request: Request,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> dict:
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > 1_000_000:
            raise HTTPException(status_code=413, detail="webhook body exceeds limit")
    try:
        result = apply_github_webhook(
            session, secret=settings.github_webhook_secret,
            required_check=settings.github_required_check,
            delivery_id=request.headers.get("X-GitHub-Delivery", ""),
            event_type=request.headers.get("X-GitHub-Event", ""),
            signature=request.headers.get("X-Hub-Signature-256", ""),
            body=bytes(body),
        )
    except WebhookAuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except WebhookInvalidError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    session.commit()
    return result
