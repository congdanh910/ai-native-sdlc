"""Health checks shared by `sdlc doctor` and the API readiness endpoint."""

from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
import subprocess

import httpx
from sqlalchemy import Engine, text

from controller.app import capacity, migrations
from controller.app.compose import run_compose
from controller.app.config import ENV_FILE, PLACEHOLDER, Settings
from controller.app.tunnel import tunnel_status

OK, WARN, FAIL = "ok", "warn", "fail"


@dataclass(frozen=True)
class Check:
    name: str
    status: str
    detail: str
    hint: str = ""

    def as_dict(self) -> dict:
        return asdict(self)


def _guard(name: str, hint: str, func: Callable[[], Check]) -> Check:
    try:
        return func()
    except Exception as exc:  # a failing check must never break the report
        return Check(name, FAIL, _short_error(exc), hint)


def _short_error(exc: Exception) -> str:
    """First line of the innermost error, without library boilerplate."""
    root = exc.orig if getattr(exc, "orig", None) is not None else exc
    message = str(root).strip().splitlines()[0] if str(root).strip() else type(root).__name__
    return message[:200]


def env_file_placeholders(path: Path = ENV_FILE) -> list[str]:
    """Keys in .env whose value still contains CHANGE_ME, including keys only compose reads."""
    keys = []
    for line in path.read_text().splitlines():
        key, sep, value = line.partition("=")
        if sep and not line.lstrip().startswith("#") and PLACEHOLDER in value:
            keys.append(key.strip())
    return keys


def check_config(settings: Settings) -> Check:
    if not ENV_FILE.is_file():
        return Check("config", FAIL, ".env is missing", "Run ./sdlc init")
    missing = sorted(set(settings.placeholders()) | set(env_file_placeholders()))
    if missing:
        return Check("config", FAIL, "placeholder values: " + ", ".join(missing),
                     "Edit .env or rerun ./sdlc init with the GX10 options")
    problems = settings.gx10_problems()
    if problems:
        return Check("config", FAIL, "; ".join(problems), "Edit the GX10_* values in .env")
    return Check("config", OK, f"operator {settings.sdlc_operator}")


def check_docker() -> Check:
    def run() -> Check:
        result = subprocess.run(["docker", "info", "--format", "{{.ServerVersion}}"],
                                capture_output=True, text=True, timeout=15, check=False)
        if result.returncode:
            return Check("docker", FAIL, "Docker is not reachable", "Start Docker Desktop")
        return Check("docker", OK, f"engine {result.stdout.strip()}")
    return _guard("docker", "Install or start Docker Desktop", run)


def check_postgres(engine: Engine) -> Check:
    def run() -> Check:
        with engine.connect() as connection:
            version = connection.execute(text("show server_version")).scalar_one()
        return Check("postgres", OK, f"server {version}")
    return _guard("postgres", "Run ./sdlc up", run)


def check_migrations(engine: Engine) -> Check:
    def run() -> Check:
        current, head = migrations.current_revision(engine), migrations.head_revision()
        if current != head:
            return Check("migrations", FAIL, f"database at {current}, code at {head}", "Run ./sdlc up")
        return Check("migrations", OK, f"at {head}")
    return _guard("migrations", "Run ./sdlc up", run)


def check_langflow(settings: Settings) -> Check:
    def run() -> Check:
        response = httpx.get(f"{settings.langflow_url}/health_check", timeout=5)
        body = response.json()
        if response.status_code != 200 or body.get("db") != "ok":
            return Check("langflow", FAIL, f"HTTP {response.status_code}: {body}", "Check ./sdlc logs langflow")
        return Check("langflow", OK, settings.langflow_url)
    return _guard("langflow", "Run ./sdlc up", run)


def check_tunnel(settings: Settings) -> Check:
    status = tunnel_status(settings)
    port = settings.ollama_tunnel_port
    if status.state == "owned":
        return Check("tunnel", OK, f"127.0.0.1:{port} (started by sdlc, pid {status.pid})")
    if status.state == "external":
        return Check("tunnel", OK, f"127.0.0.1:{port} (opened outside sdlc)")
    return Check("tunnel", FAIL, f"nothing listens on 127.0.0.1:{port}", "Run ./sdlc up")


def check_ollama(settings: Settings) -> Check:
    def run() -> Check:
        response = httpx.get(f"{settings.ollama_url}/api/tags", timeout=8)
        response.raise_for_status()
        names = {model.get("name") for model in response.json().get("models", [])}
        if settings.ollama_model not in names:
            return Check("ollama", FAIL, f"model {settings.ollama_model} not found; have {sorted(names)}",
                         "Pull the model on GX10 or change OLLAMA_MODEL")
        return Check("ollama", OK, f"{settings.ollama_model} available")
    return _guard("ollama", "Check the tunnel and the GX10 Ollama container", run)


def check_langflow_to_ollama(settings: Settings) -> Check:
    url = f"{settings.ollama_url_from_langflow}/api/tags"
    code = f"import urllib.request; urllib.request.urlopen({url!r}, timeout=8)"

    def run() -> Check:
        result = run_compose("exec", "-T", "langflow", "python", "-c", code, timeout=30)
        if result.returncode and "not running" in result.stderr:
            return Check("langflow->ollama", FAIL, "Langflow container is not running", "Run ./sdlc up")
        if result.returncode:
            return Check("langflow->ollama", FAIL, f"Langflow cannot reach {url}",
                         "The tunnel must listen on 127.0.0.1; check Docker host networking")
        return Check("langflow->ollama", OK, url)
    return _guard("langflow->ollama", "Run ./sdlc up", run)


def check_gx10_capacity(settings: Settings) -> Check:
    def run() -> Check:
        sample = capacity.sample(settings)
        detail = f"{sample.available_mib} MiB free, GPU {sample.gpu_util_percent}%"
        reasons = capacity.evaluate(sample, min_available_mib=settings.gx10_min_available_mib,
                                    max_gpu_util=settings.gx10_max_gpu_util)
        if reasons:
            return Check("gx10-capacity", WARN, f"{detail}; " + "; ".join(reasons),
                         "Model calls will wait until GX10 has capacity")
        return Check("gx10-capacity", OK, detail)
    result = _guard("gx10-capacity", "Check SSH access to GX10", run)
    # Capacity is informational here; the worker enforces it before each model call.
    return result if result.status != FAIL else Check(result.name, WARN, result.detail, result.hint)


def check_api(settings: Settings) -> Check:
    def run() -> Check:
        response = httpx.get(f"{settings.api_url}/health/live", timeout=3)
        response.raise_for_status()
        return Check("api", OK, settings.api_url)
    return _guard("api", "Run ./sdlc up", run)


def readiness_checks(settings: Settings, engine: Engine) -> list[Check]:
    """Fast checks for /health/ready."""
    return [check_postgres(engine), check_migrations(engine), check_langflow(settings),
            check_tunnel(settings), check_ollama(settings)]


def doctor_checks(settings: Settings, engine: Engine) -> list[Check]:
    config = check_config(settings)
    if config.status == FAIL:
        return [config]
    return [config, check_docker(), *readiness_checks(settings, engine),
            check_langflow_to_ollama(settings), check_gx10_capacity(settings), check_api(settings)]


def overall(checks: list[Check]) -> str:
    statuses = {check.status for check in checks}
    return FAIL if FAIL in statuses else WARN if WARN in statuses else OK
