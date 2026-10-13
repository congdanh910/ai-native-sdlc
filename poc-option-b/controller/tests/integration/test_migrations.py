"""Runs against a real PostgreSQL: SDLC_TEST_DATABASE_URL=postgresql+psycopg://... pytest -m integration"""

import os

import pytest
from sqlalchemy import create_engine

from controller.app import migrations

pytestmark = pytest.mark.integration
URL = os.environ.get("SDLC_TEST_DATABASE_URL")


@pytest.mark.skipif(not URL, reason="SDLC_TEST_DATABASE_URL is not set")
def test_upgrade_reaches_head():
    engine = create_engine(URL)
    migrations.upgrade_to_head(engine)
    assert migrations.current_revision(engine) == migrations.head_revision()
