"""Controller API. B1 serves health endpoints and the status page."""

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse

from controller.app import health
from controller.app.config import get_settings
from controller.app.db import get_engine

WEB_DIR = Path(__file__).resolve().parents[1] / "web"

app = FastAPI(title="AI-native SDLC controller", version="0.2.0")


@app.get("/health/live")
def live() -> dict:
    return {"status": health.OK}


@app.get("/health/ready")
def ready() -> JSONResponse:
    checks = health.readiness_checks(get_settings(), get_engine())
    status = health.overall(checks)
    return JSONResponse(
        {"status": status, "checks": [check.as_dict() for check in checks]},
        status_code=503 if status == health.FAIL else 200,
    )


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(WEB_DIR / "index.html")
