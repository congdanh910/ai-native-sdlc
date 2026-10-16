import pytest
from sqlalchemy import text

from controller.app import coordinator, migrations
from controller.app.db import get_engine
from controller.app.projects import registry
from controller.tests.conftest import TEST_DB

TABLES = ("events", "approvals", "pull_requests", "executions", "tasks", "plans", "decisions", "clarifications", "documents", "sources",
          "artifacts", "runs", "eval_runs", "projects")


@pytest.fixture(scope="session")
def database():
    if not TEST_DB:
        pytest.skip("SDLC_TEST_DATABASE_URL is not set")
    engine = get_engine()
    with engine.begin() as connection:
        connection.execute(text("drop schema public cascade; create schema public"))
    migrations.upgrade_to_head(engine)
    return engine


@pytest.fixture
def db(database, tmp_path, monkeypatch):
    with database.begin() as connection:
        connection.execute(text(f"truncate {', '.join(TABLES)} restart identity cascade"))
    data = tmp_path / "data"
    monkeypatch.setattr(registry, "DATA_DIR", data)
    monkeypatch.setattr(coordinator, "DATA_DIR", data)
    from controller.app import joblog
    from controller.app.discovery import service as discovery_service
    monkeypatch.setattr(discovery_service, "DATA_DIR", data)
    monkeypatch.setattr(joblog, "DATA_DIR", data)  # job logs of tests never land in the platform's .data/logs
    return database
