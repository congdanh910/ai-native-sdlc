"""Controller API and web control room (loopback only)."""

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from controller.app import health
from controller.app.api.routes import router
from controller.app.config import get_settings
from controller.app.db import get_engine

WEB_DIR = Path(__file__).resolve().parents[1] / "web"
CLIENT_HEADER = "X-SDLC-Client"

app = FastAPI(title="AI-native SDLC controller", version="0.3.0")


@app.middleware("http")
async def local_only(request: Request, call_next):
    """Defend the loopback API against other web pages open in the same browser.

    A wrong Host header means DNS rebinding; a missing custom header on a write means a
    cross-site form or fetch, since browsers cannot add it without a CORS preflight that
    this API never grants.
    """
    port = get_settings().sdlc_api_port
    allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}", "testserver"}
    if request.headers.get("host") not in allowed_hosts:
        return JSONResponse({"detail": "unexpected Host header"}, status_code=403)
    if request.method not in ("GET", "HEAD", "OPTIONS") and request.headers.get(CLIENT_HEADER) != "web":
        return JSONResponse({"detail": f"missing {CLIENT_HEADER} header"}, status_code=403)
    return await call_next(request)


app.include_router(router)
app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")


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
