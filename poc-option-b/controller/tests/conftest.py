import os
from pathlib import Path
import shutil
import subprocess

import pytest

from controller.app.config import PROJECT_ROOT, Settings

# Tests never call a model, whatever the operator's .env says.
os.environ["SDLC_TEAM_MODE"] = "mock"
os.environ["SDLC_DOING_MODE"] = "mock"
os.environ["SDLC_ENGINES"] = ""  # never route a test to a real CLI
os.environ["SDLC_CONTAINER_PREFIX"] = "sdlc-test-"  # never the containers of the platform's own runs

TEST_DB = os.environ.get("SDLC_TEST_DATABASE_URL")
if TEST_DB:
    # Must happen before anything calls get_settings(), so every session uses the test database.
    os.environ["DATABASE_URL"] = TEST_DB


@pytest.fixture
def settings(tmp_path, monkeypatch) -> Settings:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    identity = tmp_path / "id_test"
    identity.write_text("not a real key")
    return Settings(
        _env_file=None,
        sdlc_operator="tester",
        sdlc_db_password="pw",
        langflow_api_key="key",
        gx10_ssh_host="cdp@gx10.example",
        gx10_ssh_port=2222,
        gx10_ssh_identity_file=str(identity),
    )


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout


@pytest.fixture
def sample_repo(tmp_path) -> Path:
    """A local repository onboarded like examples/task-board."""
    repo = tmp_path / "task-board"
    shutil.copytree(PROJECT_ROOT / "examples" / "task-board", repo)
    (repo / "src").mkdir()
    (repo / "src" / "tasks.js").write_text("export function addTask(list, title) { return [...list, {title}]; }\n")
    (repo / "src" / "app.js").write_text("import { addTask } from './tasks.js';\n")
    (repo / "test").mkdir()
    (repo / "test" / "tasks.test.js").write_text("// existing tests\n")
    git(repo, "init", "--quiet", "-b", "main")
    git(repo, "add", ".")
    git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "--quiet", "-m", "initial")
    return repo


@pytest.fixture(autouse=True)
def no_provider_state(request, monkeypatch):
    """Tests without the database fixture see no AI provider settings and no provider out of quota (rows left by an
    earlier integration test must not reroute a unit test's executor)."""
    if "db" in request.fixturenames:
        return
    from controller.app import providers
    monkeypatch.setattr(providers, "load", lambda: {"thinking": {}, "doing": {}, "fallback": True, "models": {}})
    monkeypatch.setattr(providers, "limits", lambda now=None: {})
    monkeypatch.setattr(providers, "mark_limited", lambda engine, message: providers.reset_time(message))
