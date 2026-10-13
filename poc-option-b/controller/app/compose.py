"""docker compose invocations for the local services."""

import subprocess

from controller.app.config import ENV_FILE, PROJECT_ROOT

SERVICES = ("controller-db", "langflow-db", "langflow")


def compose_argv(*args: str) -> list[str]:
    return ["docker", "compose", "--project-directory", str(PROJECT_ROOT),
            "--env-file", str(ENV_FILE), *args]


def run_compose(*args: str, timeout: float = 600, capture: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(compose_argv(*args), cwd=PROJECT_ROOT, capture_output=capture,
                          text=True, timeout=timeout, check=False)
