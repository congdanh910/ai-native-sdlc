"""docker compose invocations for the local services."""

import subprocess

from controller.app.config import ENV_FILE, PROJECT_ROOT

SERVICES = ("controller-db", "langflow-db", "langflow")
QWEN_SERVICES = ("langflow-db", "langflow")  # needed only when SDLC_QWEN_ENABLED=true


def services(qwen_enabled: bool) -> tuple[str, ...]:
    return SERVICES if qwen_enabled else tuple(s for s in SERVICES if s not in QWEN_SERVICES)


def compose_argv(*args: str) -> list[str]:
    return ["docker", "compose", "--project-directory", str(PROJECT_ROOT),
            "--env-file", str(ENV_FILE), *args]


def run_compose(*args: str, timeout: float = 600, capture: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(compose_argv(*args), cwd=PROJECT_ROOT, capture_output=capture,
                          text=True, timeout=timeout, check=False)
